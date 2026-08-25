"""角色生成器：从多站点检索资料并蒸馏成可加载的 role prompt"""
from . import sources
from .distill import distill_character, render_skill_prompt