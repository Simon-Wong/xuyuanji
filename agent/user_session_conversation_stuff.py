class Conversation:
    id:str
    ref_count:int=0
    flag:bool=True
    caption:str=""

    def __init__(self,conversation_id:str,caption:str|None=None):
        self.id=conversation_id
        self.ref_count=0
        self.flag=True
        if caption is not None:
            self.caption=caption
        else:
            self.caption="无标题"

    def reference(self)->None:
        self.ref_count+=1
        self.flag=True

    def release(self)->None:
        self.ref_count-=1
        if self.ref_count<=0:
            self.flag=False

    def is_opened(self)->bool:
        return self.flag 

class UserSessionConversationManager:
    '''
    记录用户会话打开了哪些对话
    当用户关闭会话时，需要对该对话的计数减一，便于其他组件释放资源
    '''
    conversations:dict[str, Conversation]={}#对话id到对话
    uid2cids:dict[str,list[str]]={}#用户id到多个对话id
    uid2sid2cids:dict[str,dict[str,list[str]]]={}#用户id到会话id到多个对话id

    def __init__(self):
        self.conversations={}
        self.uid2cids={}
        self.uid2sid2cids={}

    def record_user_session(self,user_id:str,session_id:str):
        '''
        记录用户会话，初始化对应的对话列表容器
        若该用户会话已存在，则保留原有对话列表，不做覆盖
        '''
        user_session_map = self.uid2sid2cids.setdefault(user_id, {})
        user_session_map.setdefault(session_id, [])

    def record_user_session_conversation(self,user_id:str,session_id:str,conversation_id:str,conversation_caption:str|None=None)->tuple[bool,str]:
        '''
        记录用户在会话里打开了对话
        '''
        #self.record_user_session(user_id, session_id)#这个由外部保证

        cids=self.uid2sid2cids[user_id][session_id]
        if conversation_id in cids:#会话内的对话不能重复打开
            return False,"对话已打开"
        
        tc=self.conversations.get(conversation_id)
        if tc is None:
            tc=Conversation(conversation_id,conversation_caption)
            self.conversations[conversation_id]=tc
            self.uid2cids.setdefault(user_id,[]).append(conversation_id)

        tc.reference()

        self.uid2sid2cids[user_id][session_id].append(conversation_id)
        return True,""

    def get_conversations_id(self,userid:str)->list[str]:
        '''
        获取用户拥有的对话id
        '''
        return self.uid2cids.get(userid,[])
    
    def get_conversation_caption(self,user_id:str,conversation_id:str)->tuple[bool,str]:
        '''
        获取对话标题
        '''

        cids=self.uid2cids.get(user_id,[])
        if conversation_id not in cids:
            return False,"用户不存在此对话"

        conversation=self.conversations.get(conversation_id)
        if conversation is None:
            return False,"对话不存在"
        
        return True,conversation.caption

    def remove_conversation(self,user_id:str,session_id:str,conversation_id:str)->str:
        '''
        移除对话
        '''
        if conversation_id in self.conversations:
            self.conversations.pop(conversation_id)

        cids=self.uid2cids[user_id]
        if conversation_id in cids:
            cids.remove(conversation_id)

        cids2=self.uid2sid2cids[user_id][session_id]
        if conversation_id in cids2:
            cids2.remove(conversation_id)

        return conversation_id
    
    def close_session(self,user_id:str,session_id:str)->list[str]:
        '''
        关闭会话，返回因关闭会话而关闭的所有对话id
        '''
        tmp=[]

        cids=self.uid2sid2cids[user_id].pop(session_id)
        for cid_id in cids:
            c=self.conversations[cid_id]
            c.release()
            if c.is_opened()==False:
                tmp.append(cid_id)

        return tmp

    def close_conversation(self,user_id:str,session_id:str,conversation_id:str)->tuple[bool,str]:
        '''
        关闭对话
        如果对话正在使用，返回False,"对话正在使用"
        如果对话未被使用，返回True,"对话id"
        如果对话不存在，返回False,"对话不存在"
        '''
        cids=self.uid2sid2cids[user_id][session_id]
        if conversation_id not in cids:
            return False,"对话不存在"
        
        cids.remove(conversation_id)

        c=self.conversations[conversation_id]
        c.release()
        if c.is_opened()==False:
            return True,conversation_id
        else:
            return False,"对话正在使用"