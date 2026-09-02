import sys
sys.path.insert(0,'.')
from core.body_tools import express_body, merge_express, EXPRESS_BODY_TOOL
t=EXPRESS_BODY_TOOL['function']
assert t['name']=='express_body'
props=t['parameters']['properties']
assert 'emotion' in props and 'actions' in props and 'intensity' in props
print('schema OK')
express_body('害羞',['wink','nod'],'shame01',0.9)
b=merge_express({'emotion':{'primary':'平静','intensity':0.5},'actions':['nod']})
assert b['emotion']['primary']=='害羞', b
assert b['emotion'].get('intensity')==0.9, b
assert b['expression']=='shame01', b
assert b['actions'][0]=='wink' and 'nod' in b['actions'], b
print('merge OK ->', b)
r=merge_express({'emotion':{'primary':'开心'},'actions':['wave']})
assert r['emotion']['primary']=='开心' and r['actions']==['wave']
print('no-pending identity OK')
print('ALL body_tools tests pass')