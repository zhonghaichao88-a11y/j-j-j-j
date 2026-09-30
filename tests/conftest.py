import os,tempfile
# 测试不能读写真实的 v7_params.json。
os.environ['ALPHA_V7_PARAMS_FILE']=os.path.join(tempfile.mkdtemp(prefix='v7params'),'v7_params.json')

# 测试不启动行情记录器（它会连网）。
os.environ.setdefault('ALPHA_RECORDER_AUTOSTART','0')
os.environ.setdefault('ALPHA_RECORDER_CONFIG',os.path.join(tempfile.mkdtemp(prefix='rec'),'recorder_config.json'))
os.environ.setdefault('ALPHA_RECORDER_DIR',os.path.join(tempfile.mkdtemp(prefix='recdata'),'recorder_data'))
# 方案二的逐币状态文件也放到临时目录。
os.environ.setdefault('ALPHA_V7_SCHEME2_STATE',os.path.join(tempfile.mkdtemp(prefix='s2'),'v7_scheme2_state.json'))
