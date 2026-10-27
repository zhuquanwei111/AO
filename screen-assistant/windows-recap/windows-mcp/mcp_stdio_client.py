"""用官方MCP客户端连接现成Windows-MCP；不实现截图或键鼠底层。"""
import asyncio
import argparse
import base64
import json
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent
TOOLS = {'Screenshot', 'Snapshot', 'Click', 'Type', 'Scroll', 'Shortcut', 'Wait', 'App', 'ControlStatus'}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=Path, help='从UTF-8 JSONL文件读取请求，避免PTY输入干扰')
    args = parser.parse_args()
    windows_mcp_python=os.environ.get('BACKSTAGE_WINDOWS_MCP_PYTHON')
    observations=os.environ.get('BACKSTAGE_MCP_OBSERVATIONS_DIR')
    if not windows_mcp_python or not Path(windows_mcp_python).is_file():
        raise RuntimeError('windows_mcp_runtime_missing')
    if not observations:
        raise RuntimeError('mcp_observations_dir_missing')
    observations_dir=Path(observations).resolve()
    try: observations_dir.relative_to(ROOT.resolve())
    except ValueError: pass
    else: raise RuntimeError('mcp_observations_inside_install')
    observations_dir.mkdir(parents=True,exist_ok=True)
    params = StdioServerParameters(
        command=str(Path(windows_mcp_python).resolve()),
        args=['-m', 'windows_mcp', 'serve', '--config', str(ROOT / 'config.toml'),
              '--tools', ','.join(sorted(TOOLS))],
        cwd=ROOT,
        env={'ANONYMIZED_TELEMETRY': 'false', 'POSTHOG_API_KEY': '',
             'WINDOWS_MCP_SCREENSHOT_BACKEND': 'pillow', 'PYTHONUTF8': '1',
             'PYTHON_DOTENV_DISABLED': 'true'},
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=60)) as session:
            await session.initialize()
            available = (await session.list_tools()).tools
            actual = {tool.name for tool in available}
            if actual != TOOLS:
                raise RuntimeError(f'tool_whitelist_mismatch: {sorted(actual)}')
            emit({'ready': True, 'tools': sorted(actual)})
            if args.requests:
                # 保留服务端接管保护，先自然等待桌面空闲；不是缩短或绕过保护。
                await asyncio.sleep(12)
            source = args.requests.open(encoding='utf-8-sig') if args.requests else sys.stdin
            while line := await asyncio.to_thread(source.readline):
                try:
                    request = json.loads(line)
                    if request.get('tool') not in TOOLS:
                        raise ValueError('tool_not_allowed')
                    result = await session.call_tool(request['tool'], request.get('arguments', {}))
                    output = []
                    for block in result.content:
                        if block.type == 'text':
                            output.append({'text': block.text})
                        elif block.type == 'image':
                            extension = 'png' if block.mimeType == 'image/png' else 'jpg'
                            path = observations_dir / f'{uuid.uuid4().hex}.{extension}'
                            path.write_bytes(base64.b64decode(block.data))
                            output.append({'image_path': str(path), 'mime_type': block.mimeType})
                    emit({'is_error': bool(result.isError), 'content': output})
                    if result.isError and args.requests:
                        break
                except Exception as error:
                    emit({'is_error': True, 'error': str(error)})
                    if args.requests:
                        break
            if args.requests:
                source.close()


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdin.reconfigure(encoding='utf-8')
    asyncio.run(main())
