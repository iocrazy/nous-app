import httpx
from notion_client import Client
from notion_client.errors import APIResponseError
import ssl

# 创建自定义 HTTP 客户端，添加超时和连接限制
http_client = httpx.Client(
    http2=True,  # 启用 HTTP/2
    verify=True,  # 验证 SSL
    timeout=30,  # 设置请求超时时间
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)  # 控制连接数
)

# 创建 Notion 客户端
notion = Client(auth="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", client=http_client)

# 准备要创建的页面数据
new_page_data = {
    "parent": {
        "database_id": "17c75c5fd44f80f9bf8eca3d93b3b57d"
    },

    "properties": {
        "Name": {
            "title": [
                {
                    "text": {
                        "content": "test",
                    }
                }
            ]
        }

    }
}

try:
    response = notion.pages.create(**new_page_data)
    print("Page created successfully:", response)
except APIResponseError as e:
    print("Notion API error:", e)
except ssl.SSLError as ssl_error:
    print("SSL Error:", ssl_error)
except Exception as ex:
    print("An unexpected error occurred:", ex)