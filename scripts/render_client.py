"""Local template adapter; shared resources are data, never executable code."""
import json,re
from shared_data import require,json_bytes,records,digest,deterministic_zip

def render(root,client,payload,contract):
    data=payload["platforms"][client]
    base=(root/'config/base.conf').read_text(encoding='utf-8')
    require(base.count('{{RULES}}')==1 and 'RULE-SET,' not in base and 'DOMAIN-SET,' not in base, 'Invalid Shadowrocket template')
    require(re.findall(r'^\[([^]]+)\]$',base,re.M)==['General','Rule','Host','URL Rewrite'], 'Invalid sections')
    require('update-url = https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/release/shadowrocket.conf' in base,'Wrong update URL')
    return {'shadowrocket.conf':(base.replace('{{RULES}}',data['rules_text'].strip()).rstrip()+'\n').encode()}
