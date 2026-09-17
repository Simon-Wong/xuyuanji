from openai_agents_providers import OllamaProvider

class ModelStore:
    DEFAULT_MODEL:str= "qwen3:4b"
    DEFAULT_URL:str= "http://192.168.0.119:11434/v1"

    all_models:dict[str,OllamaProvider]
    default_model:str
    default_url:str

    def __init__(self):
        self.default_model = self.DEFAULT_MODEL
        self.default_url = self.DEFAULT_URL
        self.all_models={}
        self._register_model(self.default_model, self.default_url)
    def _make_key(self,model_name: str, base_url: str)->str:
        return f"{model_name}@{base_url}"
    
    def _register_model(self, model_name: str, base_url: str):
        key=self._make_key(model_name, base_url)
        self.all_models[key]=OllamaProvider(model=model_name,base_url=base_url)

    def get_model(self, model_name: str, base_url: str|None)->OllamaProvider:
        if base_url is None:
            base_url=self.default_url
        key=self._make_key(model_name, base_url)
        if key not in self.all_models:
            self._register_model(model_name, base_url)
        return self.all_models.get(key)