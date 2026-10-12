from docx import Document
from pathlib import Path
p=Path(r'C:\Users\admin\Documents\ChatGPT\多智能体搭建\多智能体协同开发系统搭建方案_MVP版.docx')
d=Document(p)
for para in d.paragraphs:
    if para.text == '6. 复用、自研与二期边界':
        para.paragraph_format.page_break_before=True
        break
d.save(p)
print('saved',p)
