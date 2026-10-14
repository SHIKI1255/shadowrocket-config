"""Local template adapter; shared resources are data, never executable code."""
import json,re
from shared_data import require,json_bytes,records,digest,deterministic_zip

HOME_LAN_NODE = '家庭局域网 = socks5, 192.168.5.4, 7897, , , udp-relay=false, tfo=false'


def shadowrocket(base,data):
    require(base.count('{{RULES}}')==1 and 'RULE-SET,' not in base and 'DOMAIN-SET,' not in base, 'Invalid Shadowrocket template')
    require(re.findall(r'^\[([^]]+)\]$',base,re.M)==['General','Proxy','Rule','Host','URL Rewrite'], 'Invalid sections')
    # One explicitly approved, credential-free LAN endpoint; not a general node loader.
    proxy=base.split('[Proxy]\n',1)[1].split('\n[',1)[0]
    require([line for _,line in records(proxy)]==[HOME_LAN_NODE], 'Unexpected proxy node or credentials')
    require('update-url = https://raw.githubusercontent.com/SHIKI1255/shadowrocket-config/release/shadowrocket.conf' in base,'Wrong update URL')
    return {'shadowrocket.conf':(base.replace('{{RULES}}',data['rules_text'].strip()).rstrip()+'\n').encode()}


def render(root,client,payload,contract):
    return shadowrocket((root/'config/base.conf').read_text(encoding='utf-8'),payload['platforms'][client])
