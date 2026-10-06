from app.ai.prompt.builder_prompt import BuilderPromptYaml

"""
评审团名册：谁是谁、各自盯什么、各自的提示词

单独一个模块，纯粹是为了打断循环导入：
记录库要拿中文名渲染记录，发言节点要提示词，接话/判定要关注点 ——
谁都能从这里取，谁都不必去 import 别的节点。
"""

# 评审角色 -> 提示词文件
REVIEWER_FILES = {
    "tech": "review/tech.yaml",
    "cost": "review/cost.yaml",
    "compliance": "review/compliance.yaml",
    "user": "review/user.yaml",
}

# 评审角色 -> 中文名，前端气泡和记录里显示
REVIEWER_NAMES = {
    "tech": "技术评审",
    "cost": "成本与进度评审",
    "compliance": "合规与伦理评审",
    "user": "用户与价值评审",
}

# 各评审的关注点。接话时要把它直接写进提示，否则弱模型容易问成别人的关注点
REVIEWER_CONCERNS = {
    "tech": "技术可行性、是否过度设计、有没有更简单的替代方案、关键难点能否落地",
    "cost": "预算是否够、接口调用费用、人力是否够、周期是否现实",
    "compliance": "数据来源是否合法、用户隐私、是否取得授权、算法偏见",
    "user": "谁真的会用、相比现有方案的优势、是不是伪需求",
}

# 提示词只读一次，避免每次发言都读盘
PROMPTS = {role: BuilderPromptYaml.get_prompt(f) for role, f in REVIEWER_FILES.items()}
