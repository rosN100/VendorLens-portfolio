"""A resumable LangGraph whose checkpoints contain references, never document text."""
from typing import TypedDict
from uuid import UUID
from sqlalchemy import select,update
from langgraph.graph import StateGraph,START,END
from langgraph.types import interrupt
from app.components.document_processing.models import ProcessingJob,ProcessingRun

class WorkflowState(TypedDict,total=False):
    run_id:str
    job_id:str|None
    blocked:bool
    error_code:str|None
    revision:int|None

def build_workflow(service,run,actor,correlation_id,saver):
    def select_job(state):
        with service.engine.connect() as connection:
            job=connection.execute(select(ProcessingJob.__table__).where(ProcessingJob.run_id==run['run_id'],ProcessingJob.status.not_in(['validated','review_required','failed'])).order_by(ProcessingJob.job_id).limit(1)).mappings().one_or_none()
        if job is None:return {'job_id':None,'blocked':False}
        service._prepare_job(dict(job))
        return {'job_id':str(job['job_id']),'blocked':False,'error_code':None}
    def stage(name):
        def execute(state):
            return service._graph_stage(name,run,UUID(state['job_id']),actor,correlation_id)
        return execute
    def after_stage(state,next_stage):
        if state.get('blocked'):return 'pause'
        with service.engine.connect() as connection:
            status=connection.scalar(select(ProcessingJob.status).where(ProcessingJob.job_id==UUID(state['job_id'])))
        return 'select_job' if status in {'validated','review_required','failed'} else next_stage
    def pause(state):
        with service.engine.begin() as connection:
            connection.execute(update(ProcessingRun).where(ProcessingRun.run_id==run['run_id']).values(status='blocked',error_code=state.get('error_code')))
        interrupt({'reason':state.get('error_code'),'job_id':state['job_id'],'action':'retry_same_request'})
        return {'blocked':False,'error_code':None}
    def reconcile(state):return {'revision':service._append(run,actor,correlation_id)}
    graph=StateGraph(WorkflowState)
    graph.add_node('select_job',select_job)
    for name in ('parse','extract','validate'):graph.add_node(name,stage(name))
    graph.add_node('pause',pause);graph.add_node('reconcile',reconcile)
    graph.add_edge(START,'select_job')
    graph.add_conditional_edges('select_job',lambda s:'parse' if s.get('job_id') else 'reconcile',{'parse':'parse','reconcile':'reconcile'})
    for current,next_stage in (('parse','extract'),('extract','validate'),('validate','select_job')):
        graph.add_conditional_edges(current,lambda s,n=next_stage:after_stage(s,n),{'pause':'pause','select_job':'select_job',next_stage:next_stage})
    graph.add_edge('pause','select_job');graph.add_edge('reconcile',END)
    return graph.compile(checkpointer=saver)
