import numpy as np, torch, gsplat, json, math, sys
from PIL import Image
import torch.nn.functional as F
d=np.load('3dgs_v1/model.npz')
means=torch.tensor(d['means'],dtype=torch.float32,device='cuda')
scales=torch.tensor(d['scales'],dtype=torch.float32,device='cuda')
q=torch.tensor(d['quats'],dtype=torch.float32,device='cuda')  # xyzw
quats=torch.cat([q[:,3:],q[:,:3]],1)  # wxyz
rgb=torch.tensor(d['rgb'],dtype=torch.float32,device='cuda').clamp(0,1)
op=torch.tensor(d['opacity'],dtype=torch.float32,device='cuda')
K=torch.tensor(d['K'],dtype=torch.float32,device='cuda')
H,W=int(d['H']),int(d['W'])
poses=json.load(open('views_all/poses.json'))
pts=np.array([p['eye'] for p in poses])
up=torch.tensor([0.,0.,1.],dtype=torch.float32,device='cuda')
frames=[]
# path: walkthrough along main aisle + orbit
t_end=15.0; fps=30
path=[]
for k in range(int(t_end*fps)):
    t=k/(t_end*fps)
    # sweep x along center aisle y=0-ish, looking forward
    x=np.linspace(pts[:,0].min()+2,pts[:,0].max()-2,1)[0]
    xx=np.linspace(pts[:,0].min()+2,pts[:,0].max()-2,int(t_end*fps))[k]
    eye=torch.tensor([xx, np.sin(t*2*np.pi)*3.0, 1.4],dtype=torch.float32,device='cuda')
    tgt=torch.tensor([xx+2.0, np.sin((t+0.08)*2*np.pi)*3.0, 1.3],dtype=torch.float32,device='cuda')
    z=eye-tgt; z=z/torch.norm(z)
    x_=torch.cross(up,z); x_=x_/torch.norm(x_)
    y_=torch.cross(z,x_)
    R=torch.stack([x_,y_,z],0); tvec=-R@eye
    T=torch.eye(4,device='cuda'); T[:3,:3]=R; T[:3,3]=tvec
    out,_,_=gsplat.rasterization(means=means,quats=quats,scales=scales,opacities=op,colors=rgb,
        viewmats=T.unsqueeze(0),Ks=K.unsqueeze(0),width=W,height=H,render_mode='RGB')
    frames.append((out[0].clamp(0,1).cpu().numpy()*255).astype(np.uint8))
import imageio
imageio.mimwrite(sys.argv[1] if len(sys.argv)>1 else '3dgs_flythrough.mp4',frames,fps=30,quality=9)
print('saved',len(frames))
