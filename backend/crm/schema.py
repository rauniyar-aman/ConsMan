import re
from drf_spectacular.openapi import AutoSchema

class StableAutoSchema(AutoSchema):
    def get_operation_id(self):
        return re.sub(r'[^a-zA-Z0-9]+','_',f'{self.method.lower()}_{self.path}').strip('_')
