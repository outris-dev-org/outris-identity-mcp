import sys
import re

file_path = r'd:\repos\outris-identity-mcp\mcp_server\tools\intent_tools.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

mapping = {
    'investigate_phone': ['general', 'fraud_analyst', 'skip_tracer'],
    'assess_fraud_risk': ['fraud_analyst'],
    'find_contacts': ['skip_tracer', 'collections'],
    'due_diligence_person_start': ['general', 'compliance'],
    'check_job': ['underwriter', 'general'],
    'investigate_email': ['general', 'fraud_analyst', 'skip_tracer'],
    'resolve_company': ['underwriter', 'general', 'compliance'],
    'lookup_gst': ['underwriter', 'compliance', 'collections'],
    'verify_pan': ['general', 'compliance', 'underwriter'],
    'lookup_vehicle': ['skip_tracer', 'collections', 'general'],
    'verify_bank_account': ['general', 'underwriter', 'collections']
}

for tool_name, personas in mapping.items():
    pattern = r'(name=[\'\"]' + tool_name + r'[\'\"],)'
    replacement = r'\1\n    allowed_personas=' + str(personas) + ','
    content = re.sub(pattern, replacement, content)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Updated intent_tools.py')
