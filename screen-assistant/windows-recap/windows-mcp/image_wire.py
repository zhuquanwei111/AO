"""仅在管道内转交 MCP 图片，不创建截图文件。"""


def serialize_image(block):
    if block.mimeType not in ('image/png', 'image/jpeg') or not isinstance(block.data, str):
        raise ValueError('invalid_image_block')
    return {'image_data': block.data, 'mime_type': block.mimeType}
