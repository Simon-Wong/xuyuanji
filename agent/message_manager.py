from defination_types import MsgHis

class MessageManager:
    all_messages:dict[str,MsgHis]
    def __init__(self):
        self.all_messages = {}

    def _make_key(self,user_id: str,conversation_id:str)->str:
        return f"{user_id}@{conversation_id}"
    def get_messages(self, user_id: str,conversation_id:str)->MsgHis:
        if user_id is None or conversation_id is None:
            return []

        key=self._make_key(user_id,conversation_id)
        item=self.all_messages.get(key,[])
        return item
    def append_message(self, user_id: str,conversation_id:str,msg:MsgHis):
        key=self._make_key(user_id,conversation_id)
        self.all_messages[key].append(msg)