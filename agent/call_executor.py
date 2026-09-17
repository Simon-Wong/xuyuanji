import json

from configuration import UserConfig
from sandbox_wrapper import SandboxWrapper
from actor_stuff import ActorData

from pre_load import tool_map


class CallExecutor:
    max_turns_try_function:int
    use_sandbox:bool=False
    sandbox:SandboxWrapper
    need_init_flag:bool=False
    sandbox_type:str
    sandbox_script_type:str
    save_dir:str
    host_scripts_dir:str

    def __init__(self,user_config:UserConfig):
        self.max_turns_try_function=user_config.max_turns_try_function
        if user_config.use_sandbox == True:
            self.use_sandbox=True
            self.sandbox=None
            self.need_init_flag=True

            self.sandbox_type=user_config.sandbox_type
            self.sandbox_script_type=user_config.sandbox_script_type
            self.save_dir=user_config.save_dir

            self.host_scripts_dir=user_config.output_dir

    def append_prompt(self)->str:
        '''
        补充提示词
        '''
        pm=""
        if self.use_sandbox:
            pm=f'''
启用了沙箱，允许运行{self.sandbox_script_type}类型的脚本。
脚本存放目录: {self.host_scripts_dir}，
数据目录: {self.save_dir}，
沙箱结果保存目录: {self.save_dir}'''
        else:
            pm=f'''
未启用沙箱，不允许运行脚本。
''' 
        return pm

    def stop(self):
        if self.sandbox is not None:
            self.sandbox.stop()

    def run_in_local(self,tc_id:str,tc_name: str,tc_args: str,data:ActorData) -> tuple[bool,ActorData]:
        flag:bool=True
        result_data=""

        try:
            tool_func=tool_map.get(tc_name)
            args=json.loads(tc_args)
            result_data=tool_func(**args)
            print(f"工具执行结果: {result_data}")
        except Exception as e:
            flag=False
            result_data = f"工具执行出错: {e}"
            print(result_data)

        data.append_callresult({"call_id": tc_id,
                            "output": result_data,
                            "type": "function_call_output"
                        })
        return flag,data

    def run_in_sandbox(self,tc_id:str,tc_name: str,tc_args: str,data:ActorData) -> tuple[bool,ActorData]:
        flag:bool=True
        result_data=""

        if self.need_init_flag:
            self.need_init_flag=False
            self.sandbox=SandboxWrapper(sandbox_type=self.sandbox_type,
                                   sandbox_script_type=self.sandbox_script_type,
                                   save_dir=self.save_dir)
            self.sandbox.start(host_scripts_dir=self.host_scripts_dir,
                               host_data_dir=self.save_dir)

        if self.sandbox is None:
            data.append_callresult({"call_id": tc_id,
                            "output": "用户没有权限使用沙箱执行脚本。请不要再尝试调用该工具。",
                            "type": "function_call_output"
                        })
            flag=False
            return flag,data
        try:
            args=json.loads(tc_args)
            
            script_name=args.get("script_name",None) #args["script_name"]
            script_args=args.get("script_args",None) #args["script_args"]

            #todo:有待调试
            #如果script_name中包含host_scripts_dir,则需要去掉。
            if self.host_scripts_dir in script_name:
                #self.host_scripts_dir的结尾要有一个'/'
                if self.host_scripts_dir.endswith("/"):
                    script_name=script_name.replace(self.host_scripts_dir,"")
                else:
                    script_name=script_name.replace(self.host_scripts_dir+"/","")
                
            result_data=self.sandbox.run(name=script_name, args=script_args)
            print(f"sandbox执行结果: {result_data}")
        except Exception as e:
            flag=False
            result_data = f"sandbox执行出错: {e}"
            print(result_data)

        data.append_callresult({"call_id": tc_id,
                            "output": result_data,
                            "type": "function_call_output"
                        })   
        
        return flag,data
    
    def save_file(self,container_path: str, host_path: str)->tuple[int, str, str]:
        return self.sandbox.save_file(container_path, host_path)
