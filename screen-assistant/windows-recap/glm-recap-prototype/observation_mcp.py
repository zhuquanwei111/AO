"""当前图片的受限回执工具；不读任意路径、不接触桌面或网络。"""
import json
import os
from pathlib import Path
from mcp.server.mcpserver import MCPServer

server=MCPServer('recap-current-observation')
job=json.loads(Path(os.environ['RECAP_GLM_JOB']).read_text(encoding='utf-8'))
calls=0


@server.tool()
def inspect_current() -> dict:
    """确认当前任务图片的回执；图片由本轮用户输入提供。只调用一次，不执行任何桌面动作。"""
    global calls
    calls+=1
    if calls>1: raise ValueError('observation_tool_budget_exhausted')
    with Path(job['audit']).open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'tool':'inspect_current','call':calls})+'\n')
    return {'observation_receipt':job['receipt'],'challenge':job.get('challenge')}


if __name__=='__main__': server.run(transport='stdio')
