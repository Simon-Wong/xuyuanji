import os
import copy
import json

from call_executor import CallExecutor
from defination_types import ProductType
from configuration import UserConfig
from actor_stuff import ActorData

from pre_load import product_tools,tool_map

class Product:
    question:str
    type:ProductType
    data:str
    file_path:str=None
    file_name:str=None
    def __init__(self,question:str,type:ProductType,data:str):
        self.question=question
        self.type=type
        self.data=data
        if self.type=="PT_FILE":
            self.file_path, self.file_name = os.path.split(data)

class WorkSpace:
    work_dir:str
    output_dir:str
    save_dir:str
    call_executor:CallExecutor
    use_sandbox:bool=False
    enable_sandbox:bool=False
    session_id:str
    products:dict[str, list[Product]]={}
    

    def __init__(self,user_config:UserConfig):
        self.session_id=user_config.session_id
        self.use_sandbox=user_config.use_sandbox
        self.work_dir=os.getcwd()
        if user_config.work_dir != "default_work_dir":
            if os.path.exists(user_config.work_dir) and os.path.isdir(user_config.work_dir):
                self.work_dir=user_config.work_dir
            else:
                print(f"错误：工作目录 {user_config.work_dir} 不存在或不是目录。使用当前目录 {self.work_dir}")
                self.work_dir=os.getcwd()


        program_dir=os.path.dirname(os.path.abspath(__file__))
        self.output_dir=os.path.join(program_dir,"output_"+user_config.user_id+"_"+user_config.session_id)
        if user_config.output_dir != "default_output_dir":
            if os.path.exists(user_config.output_dir) and os.path.isdir(user_config.output_dir) and os.access(user_config.output_dir, os.W_OK):
                self.output_dir=user_config.output_dir
            else:
                print(f"错误：输出目录 {user_config.output_dir} 不存在或不是目录或不可写。使用默认规则生成目录 {self.output_dir}")
                os.makedirs(self.output_dir, exist_ok=True)
        else:
            os.makedirs(self.output_dir, exist_ok=True)

        self.save_dir=os.path.join(program_dir,"save_"+user_config.user_id)      
        if user_config.save_dir != "default_save_dir":
            if os.path.exists(user_config.save_dir) and os.path.isdir(user_config.save_dir) and os.access(user_config.save_dir, os.W_OK):
                self.save_dir=user_config.save_dir
            else:
                print(f"错误：保存目录 {user_config.save_dir} 不存在或不是目录或不可写。使用默认规则生成目录 {self.save_dir}")
                os.makedirs(self.save_dir, exist_ok=True)
        else:
            os.makedirs(self.save_dir, exist_ok=True)

        user_config_real:UserConfig=copy.deepcopy(user_config)
        user_config_real.work_dir=self.work_dir
        user_config_real.output_dir=self.output_dir
        user_config_real.save_dir=self.save_dir
        self.call_executor=CallExecutor(user_config_real)

    def retrieve_file_name(self,tc_args: str)->str:
        args=json.loads(tc_args)
        filename=args.get("filename",None)
        if filename is None:
            return ""
        return filename

    def run(self, data:ActorData,question:str|None=None) -> ActorData:
        checklist=data.get_checklist()  

        for idx, detail, decision, reason in checklist:
            tc_id=detail[0]
            tc_name=detail[1]
            tc_args=detail[2]

            if decision == 'y':
                #self.state.approve(self.result.interruptions[idx])
                print(f"<{idx}> {detail} 审批：通过")
                if tc_name=="execute_script":
                    #执行脚本需要在沙箱中运行
                    flag,data=self.call_executor.run_in_sandbox(tc_id,tc_name,tc_args,data)#这里在内部形成了结果字符串
                else:
                    # 其他工具需要在本地运行
                    if tc_name in tool_map.keys():
                        flag,data=self.call_executor.run_in_local(tc_id,tc_name,tc_args,data)#这里在内部形成了结果字符串

                        # 生成产物
                        if tc_name in product_tools and question is not None and flag:    
                            filename=self.retrieve_file_name(tc_args)
                            self.make_product(question,"PT_FILE",filename)

                    else:
                        print(f"未知工具 {tc_name}，跳过。")
                        data.append_callresult({"call_id": tc_id,
                                            "output": f"错误：未知工具 {tc_name}",
                                            "type": "function_call_output"
                                        })
            else:
                print(f"<{idx}> {detail} 审批：拒绝 原因:{reason}")
                data.append_callresult({"call_id": tc_id,
                                "output": "用户拒绝了该工具调用。请不要再尝试调用该工具。",
                                "type": "function_call_output"
                            })
            
        return data

    def make_product(self,question:str,type:str,data:str)->str:
        # 生成产物
        if question not in self.products:
            self.products[question]=[]
        self.products[question].append(Product(question,type,data))

    def save_product(self,question:str|list|None=None)->None:
        # 保存产物
        # 将产物中的文件保存到保存目录
        listquestions=[]
        if question is None:
            listquestions=self.products.keys()
        elif type(question)==str:
            listquestions=[question]
        elif type(question)==list:
            listquestions=question

        tmpset=set()
        dictproducts:dict[str, list[Product]]={}

        for q in listquestions:
            dictproducts[q]=[]
            for p in self.products[q]:
                if p.type=="PT_FILE":
                    if p.data not in tmpset:
                        tmpset.add(p.data)
                        dictproducts[q].append(p)


        #todo:如果有很多文件和目录层级，如何保证它们的位置关系呢？例如：/a/b/c.txt 保存到 /save/a/b/c.txt
        for q in dictproducts.keys():
            for p in dictproducts[q]:
                if p.file_name.endswith(".py"):
                    self.call_executor.save_file("/scripts/"+p.file_name,os.path.join(self.save_dir,p.file_name))


    def stop(self):
        self.call_executor.stop()


    def show(self):
        print(f"工作目录: {self.work_dir}")
        print(f"输出目录: {self.output_dir}")
        print(f"保存目录: {self.save_dir}")
        print(f"是否启用沙箱: {self.call_executor.use_sandbox}")
        print(f"沙箱是否可用: {self.call_executor.sandbox is not None}")

    def append_prompt(self)->str:
        '''
        补充提示词
        '''
        pm=self.call_executor.append_prompt()
        return pm

class WorkspaceManager:
    workspaces:dict[str, dict[str, WorkSpace]]={}
    flag:bool=True
    def get_workspace(self,user_config: UserConfig,conversation_id:str)->WorkSpace:
        if self.flag==False:
            return None

        user_id=user_config.user_id
        
        if user_id not in self.workspaces:
            self.workspaces[user_id]={}
        if conversation_id not in self.workspaces[user_id]:
            self.workspaces[user_id][conversation_id]=WorkSpace(user_config)

        return self.workspaces[user_id][conversation_id]
    
    def stop_one(self,user_id:str,conversation_id:str):
        if user_id not in self.workspaces.keys():
            return
        if conversation_id not in self.workspaces[user_id].keys():
            return
        self.workspaces[user_id][conversation_id].stop()
        self.workspaces[user_id].pop(conversation_id)

    def stop_all(self):
        self.flag=False

        for user_id in self.workspaces.keys():
            for conversation_id in self.workspaces[user_id].keys():
                self.workspaces[user_id][conversation_id].stop()

        self.workspaces={}
