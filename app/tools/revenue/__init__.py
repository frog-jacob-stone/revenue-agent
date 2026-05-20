from app.tools.base import ToolDefinition
from app.tools.revenue.get_revenue_data import GET_REVENUE_DATA
from app.tools.revenue.trigger_revenue_recognition import TRIGGER_REVENUE_RECOGNITION

REVENUE_TOOLS: list[ToolDefinition] = [
    GET_REVENUE_DATA,
    TRIGGER_REVENUE_RECOGNITION,
]
