import json
from typing import Self
import os
from dataclasses import dataclass, fields

@dataclass
class UserConfig:
    '''用户配置'''
    user_id:str|None=None
    session_id:str|None=None # 会话id，默认None
    config_file_name:str|None=None # 配置文件名，默认None

    #定义可配置参数，配置文件可以包含以下字段，
    # 对于配置文件中缺少的字段会用默认值
    # 对于配置文件中未列出的字段会忽略
    max_turns_try_function:int=5 # 最大尝试次数，默认3次
    debug_need_same_answer:bool=False # 调试用，是否需要相同回答，默认False
    blabla:str="blabla"

    # def __init__(self,user_id:str,session_id:str,config_file_name:str,**kwargs):
    #     self.user_id=user_id
    #     self.session_id=session_id
    #     self.config_file_name=config_file_name

    #     self.__dict__.update(kwargs)
    
    @classmethod
    def load(cls,user_id:str,session_id:str,config_file_name:str|None=None) -> Self:
        if config_file_name:
            config_file_name=config_file_name.strip()
            file_path=config_file_name
        else:
            file_path="default_user_config.json"

        if not os.path.exists(file_path):
            file_path="default_user_config.json"

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            #raise FileNotFoundError(f"配置文件不存在: {file_path}")
            return cls(user_id=user_id,
                       session_id=session_id,
                       config_file_name=f"配置文件不存在，使用默认配置启动。")    
        except json.JSONDecodeError as e:
            #logger.warning("配置文件 %s 格式错误，使用默认配置启动。错误详情：%s", file_path, e)
            return cls(user_id=user_id,
                       session_id=session_id,
                       config_file_name=f"配置文件 {file_path} 格式错误，使用默认配置启动。错误详情：{e}")    
        
        #valid_fields = set( cls.__annotations__.keys())
        # valid_fields = set()
        # for base_cls in cls.__mro__:
        #     valid_fields.update(getattr(base_cls, "__annotations__", {}).keys())
        valid_fields = {f.name for f in fields(cls)}
        valid_fields.remove("user_id")
        valid_fields.remove("session_id")
        valid_fields.remove("config_file_name")
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        
        return cls(user_id=user_id,
                   session_id=session_id,
                   config_file_name=file_path,
                   **filtered)
    
# 测试
def Test1():
    cfg = UserConfig.load("user123","session123","user_config.json")

    print(cfg.user_id)
    print(cfg.session_id)
    print(cfg.config_file_name)

    print(cfg.debug_need_same_answer)
    print(cfg.max_turns_try_function)
    print(cfg.blabla)
    print(cfg.__dict__)

def Test2():
    cfg1 = UserConfig.load("user123","session123","user_config.json")
    cfg2 = UserConfig.load("user123456","session123456","user_config.json")

    print(cfg1.blabla)
    print(cfg1.__dict__)

    UserConfig.blabla="hahaha"
    print(cfg2.blabla)
    print(cfg2.__dict__)

def Test3():
    cfg = UserConfig("user123","session123","user_config.json")  
    print(cfg.user_id)
    print(cfg.session_id)
    print(cfg.config_file_name)

    print(cfg.debug_need_same_answer)
    print(cfg.max_turns_try_function)
    print(cfg.blabla)
    print(cfg.__dict__)


if __name__ == "__main__":
    print("=============Test1================")
    Test1()
    print("=============Test2================")
    Test2()
    print("=============Test3================")
    Test3()

