from event_bus import EventBus,Event
from conversation_backend import ConversationBackend
from actor_stuff import ActorData,ActorStatus




'''
Bus_EventHandler = Callable[["EventBus", "Event"], None]
Bus_SubHandler = Callable[[str, "Event"], None]
'''

######################################################
#创建对话后端
######################################################
BE_create_conversation_backend:str="create_conversation_backend"

def BEH_create_conversation_backend(bus:EventBus,event:Event):
    # if event.event_type==BE_create_conversation_backend:
    #     print("对话后端创建")
    cb:ConversationBackend=event.data
    print(f"对话后端创建，id为:{cb.id}")

######################################################
#创建actor_data
######################################################
BE_create_actor_data:str="create_actor_data"

def BEH_create_actor_data(bus:EventBus,event:Event):
    # if event.event_type==BE_create_actor_data:
    #     print("actor数据创建")
    actor_data:ActorData=event.data
    print(f"actor数据创建，id为:{actor_data.id}")
    
    if actor_data.status==ActorStatus.NEED_EXECUTE_CHECKLIST:
        #转发检查列表事件
        bus.trigger_event(user_id=event.user_id,session_id=event.session_id,conversation_id=event.cid,
                           object_id=event.get_actor_id(),data=actor_data,
                           trace_id=event.trace_id,
                           event_type=BE_checklist)
        
    elif actor_data.status==ActorStatus.FINAL_RESULT:
        #最终结果,给用户汇报。
        #但是按设计，要先给核验器，再给汇报器
        #这里是测试，直接打印出结果
        print(actor_data)
    else:
        #其他状态，直接打印出结果
        print(f"错误的状态:{actor_data.status}")

######################################################
BE_checklist:str="checklist"

def BEH_checklist(bus:EventBus,event:Event):
    # if event.event_type==BE_checklist:
    #     print("检查列表")
    actor_data:ActorData=event.data
    #这里假装发送了检查列表
    print(actor_data.checklist)

######################################################
BE_update_actor_data:str="update_actor_data"

def BEH_update_actor_data(bus:EventBus,event:Event):
    # if event.event_type==BE_update_actor_data:
    #     print("actor数据更新")

    actor_data:ActorData=event.data
    #这里假装获取用户修改过的检查列表
    actor_data.check_done()