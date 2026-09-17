from sandboxex import PersistentScriptSandbox

class SandboxWrapper:
    sandbox_type:str=""
    sandbox_script_type:str=""
    sandbox:PersistentScriptSandbox

    save_dir:str#调试用
    host_scripts_dir:str#调试用
    host_data_dir:str=""#调试用

    def __init__(self,sandbox_type: str,sandbox_script_type: str,save_dir: str):
        self.sandbox_type=sandbox_type

        if self.sandbox_type == "PersistentScriptSandbox":
            self.sandbox=PersistentScriptSandbox(output_dir=save_dir)
            self.sandbox_script_type=sandbox_script_type

        else:
            self.sandbox=None
            print(f"不支持的sandbox_type {sandbox_type}")

    def start(self,host_scripts_dir: str,host_data_dir: str = None,ro_volumes: list[tuple[str, str]] = None)->tuple[bool,str]:
        flag:bool =False
        if self.sandbox is None:
            return False,f"错误：不支持的sandbox_type： {self.sandbox_type}"

        if self.sandbox_script_type == "python":
            flag= self.sandbox.start_python_script(host_scripts_dir=host_scripts_dir,
                                                   host_data_dir=host_data_dir,
                                                   ro_volumes=ro_volumes)
            self.host_scripts_dir=host_scripts_dir
            self.host_data_dir=host_data_dir

        elif self.sandbox_script_type == "bash":
            flag = self.sandbox.start_bash_script(host_scripts_dir=host_scripts_dir,
                                                  ro_volumes=ro_volumes)
            self.host_scripts_dir=host_scripts_dir
        
        if flag:
            return True,""
        elif not flag:
            return False,f"错误：启动{self.sandbox_script_type}类型的PersistentScriptSandbox沙箱失败"

        return False,f"错误：不支持的sandbox_script_type {self.sandbox_script_type},仅支持python和bash"

    def stop(self):
        if self.sandbox is None:
            return f"错误：不支持的sandbox_type {self.sandbox_type}"
        self.sandbox.cleanup()

    def run(self, name:str, args:str) -> str:
        if self.sandbox is None:
            return f"错误：不支持的sandbox_type {self.sandbox_type}"
        
        if self.sandbox_script_type == "python":
            flag, outputstr, errstr = self.sandbox.run_python_script(name, args)
        elif self.sandbox_script_type == "bash":
            flag, outputstr, errstr = self.sandbox.run_bash_script(name, args)
        else:
            return f"错误：不支持的sandbox_script_type {self.sandbox_script_type},仅支持python和bash"
        
        if flag == 0:
            return outputstr
        
        return errstr

    def save_file(self,container_path: str, host_path: str)->tuple[int, str, str]:
        return self.sandbox.copy_from_container(container_path, host_path)
