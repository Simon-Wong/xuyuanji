import json
import os

class CacheManager:
    #调试用
    #保存dict[str:str]，并以json的形式保存到磁盘。
    cache:dict[str:str]
    pathfile:str

    def __init__(self):
        self.pathfile="cache.json"
        self.cache={}
        self._load_cache(self.pathfile)

    def _load_cache(self,filepath:str):      
        if os.path.exists(filepath):
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.rstrip('\n')
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            self.cache.update(obj)
                    except json.JSONDecodeError:
                        # 忽略损坏的行（可记录日志）
                        continue

    def _append_cache(self,q:str,a:str):
        #追加式写入, q和a占一行
        tmp={q:a}
        with open(self.pathfile,"a",encoding="utf-8") as f:
            json.dump(tmp,f,ensure_ascii=False)
            f.write("\n")

    def set(self,q:str,a:str):    
        tmp=self.cache.get(q)
        if tmp is None:
            self.cache[q]=a
            self._append_cache(q,a)
    
    def get(self,q:str)->str:
        tmp=self.cache.get(q)
        if tmp is None:
            return None
        print(f"<cache hit>")
        return tmp