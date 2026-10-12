"""Use the documents skill renderer with an already exported Word PDF.

Windows has no bundled LibreOffice in this dependency runtime. Word performs
DOCX conversion; render_docx.py performs the standard page rasterization.
"""
from pathlib import Path
import sys
import os
import shutil
import json

ROOT=Path(__file__).resolve().parent.parent
QA=ROOT/'_mvp_render'/'v2_qa'
DOCX=ROOT/'多智能体协同开发系统搭建方案_多Harness优化版_v2.docx'
PDF=QA/'preview.pdf'
SKILL=Path(r'C:\Users\admin\.codex\plugins\cache\openai-primary-runtime\documents\26.1007.11041\skills\documents')
BIN=Path(r'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\native\poppler\Library\bin')
os.environ['PATH']=str(BIN)+os.pathsep+os.environ.get('PATH','')
sys.path.insert(0,str(SKILL))
import render_docx

if not PDF.exists() or PDF.stat().st_mtime < DOCX.stat().st_mtime:
    raise RuntimeError('Export the current DOCX with Word before rasterization')

def word_pdf(doc_path,user_profile,convert_tmp_dir,stem,verbose):
    output=Path(convert_tmp_dir)/(stem+'.pdf')
    shutil.copy2(PDF,output)
    return str(output),'Conversion backend: native Microsoft Word PDF export'

render_docx.convert_to_pdf=word_pdf
dpi=render_docx.calc_dpi_via_ooxml_docx(str(DOCX),1600,2000)
pages=render_docx.rasterize(str(DOCX),str(QA),dpi,False,False)
from pypdf import PdfReader
reader=PdfReader(str(PDF))
summary=[{'page':i+1,'characters':len(p.extract_text() or ''),'start':(p.extract_text() or '')[:100]} for i,p in enumerate(reader.pages)]
(QA/'page_text.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pages':len(pages),'page_text':summary},ensure_ascii=True))
