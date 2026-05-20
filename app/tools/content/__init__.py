from app.tools.base import ToolDefinition
from app.tools.content.create_post import CREATE_POST
from app.tools.content.export_posts import EXPORT_POSTS
from app.tools.content.get_posts import GET_POSTS
from app.tools.content.publish_post import PUBLISH_POST
from app.tools.content.reject_post import REJECT_POST
from app.tools.content.rewrite_post import REWRITE_POST

CONTENT_TOOLS: list[ToolDefinition] = [
    CREATE_POST,
    GET_POSTS,
    REWRITE_POST,
    REJECT_POST,
    PUBLISH_POST,
    EXPORT_POSTS,
]
