import os,tempfile
# 测试不能读写真实的 v7_params.json。
os.environ['ALPHA_V7_PARAMS_FILE']=os.path.join(tempfile.mkdtemp(prefix='v7params'),'v7_params.json')
