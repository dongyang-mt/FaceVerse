import numpy as np
model_path = '/opt/data/dongyang/code/FaceVerse/data/faceverse_simple_v2.npy'
model_dict = np.load(model_path, allow_pickle=True).item()
# print the name of 52 blendshapes
print(model_dict['exp_name_list']) 
meanshape = model_dict['meanshape'].reshape(-1, 3)
tri = model_dict['tri']
meantex = model_dict['tri'].reshape(-1, 3)
expBase = model_dict['exBase']
blendshape_0_ver = meanshape + expBase[:, 0].reshape(-1, 3)

# save the model as ply format (or any other format)
from util_functions import ply_from_array_color
ply_from_array_color(blendshape_0_ver, meantex.astype(np.uint8), tri, 'blendshape_0.ply')

for i in range(expBase.shape[1]):
    blendshape_0_ver = meanshape + expBase[:, i].reshape(-1, 3)

    # save the model as ply format (or any other format)
    ply_from_array_color(blendshape_0_ver, meantex.astype(np.uint8), tri, '/opt/data/dongyang/code/FaceVerse/arkit_blendshape52/blendshape_%02d.ply'%(i))
