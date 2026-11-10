#!/usr/bin/env python3
"""CPU-only image localization against original Gaussian centers or baseline depth.
Approximate colored point rasterization is a retrieval tool, NOT the final render.
Defaults to dev references. Explicit heldout mode only uses unchanged render depth.
Pose candidates require independent actual-GS review before appearance evaluation.
"""
import os
os.environ.setdefault('OPENBLAS_NUM_THREADS','1')
import argparse,json,hashlib,math,time
from pathlib import Path
import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt
from plyfile import PlyData
cv2.setNumThreads(1)
BASE=Path('/home/nvidia/algotithm/runs/starbucks_tour/scene_bench_v1')
PLY=Path('/home/nvidia/graduation_material/hackathon_pipeline/runs/upgrade/scene_high/splats.ply')

def load_points(path):
 v=PlyData.read(path)['vertex'].data
 xyz=np.column_stack([v[n] for n in ('x','y','z')]).astype(np.float32)
 rgb=np.clip(.5+.28209479177387814*np.column_stack([v[f'f_dc_{i}'] for i in range(3)]),0,1)
 mask=np.isfinite(xyz).all(1)&(v['opacity']>-2)&(np.max(np.abs(xyz),axis=1)<25)
 return xyz[mask],(rgb[mask]*255).astype(np.uint8)

def rotation(yaw,pitch=0):
 a,b=np.radians([yaw,pitch]);forward=np.array([np.cos(a)*np.cos(b),np.sin(a)*np.cos(b),np.sin(b)])
 right=np.cross(forward,[0,0,1]);right/=np.linalg.norm(right)
 down=np.cross(forward,right)
 return np.stack([right,down,forward])

def raster(xyz,rgb,R,eye,K,w,h):
 q=(xyz-eye)@R.T
 good=q[:,2]>.1
 ids=np.flatnonzero(good);q=q[good]
 u=np.rint(q[:,0]/q[:,2]*K[0,0]+K[0,2]).astype(int)
 v=np.rint(q[:,1]/q[:,2]*K[1,1]+K[1,2]).astype(int)
 good=(u>=0)&(u<w)&(v>=0)&(v<h)
 ids=ids[good];q=q[good];pix=v[good]*w+u[good]
 order=np.argsort(q[:,2],kind='stable')
 _,first=np.unique(pix[order],return_index=True)
 winners=order[first]
 index=np.full(w*h,-1,dtype=np.int32);index[pix[winners]]=ids[winners];index=index.reshape(h,w)
 dist,nearest=distance_transform_edt(index<0,return_indices=True)
 filled=index[tuple(nearest)];filled[dist>2.5]=-1
 image=np.zeros((h,w,3),np.uint8);mask=filled>=0;image[mask]=rgb[filled[mask]]
 return image,filled

def feat(image):
 return cv2.SIFT_create(nfeatures=2500,contrastThreshold=.025).detectAndCompute(cv2.cvtColor(image,cv2.COLOR_RGB2GRAY),None)

def matches(k,d,rk,rd,ids,xyz):
 if d is None or rd is None or len(d)<8 or len(rd)<8:return None
 matcher=cv2.FlannBasedMatcher(dict(algorithm=1,trees=4),dict(checks=64))
 paired=matcher.knnMatch(d,rd,k=2)
 reverse=matcher.match(rd,d)
 reverse_index={m.queryIdx:m.trainIdx for m in reverse}
 good=[a for a,b in paired if a.distance<.72*b.distance and reverse_index.get(a.trainIdx)==a.queryIdx]
 if len(good)<8:return None
 uv=np.array([k[m.queryIdx].pt for m in good]);target=np.rint([rk[m.trainIdx].pt for m in good]).astype(int)
 point_ids=ids[target[:,1].clip(0,ids.shape[0]-1),target[:,0].clip(0,ids.shape[1]-1)]
 keep=point_ids>=0
 # Unique 3D centers avoid duplicated support inflating RANSAC consensus.
 _,unique=np.unique(point_ids[keep],return_index=True)
 return uv[keep][unique],xyz[point_ids[keep][unique]],point_ids[keep][unique]

def pnp(uv,obj,K):
 if len(uv)<10:return None
 best=None
 for factor in [.75,1.,1.25,1.5]:
  ki=K.copy();ki[0,0]*=factor;ki[1,1]*=factor
  cv2.setRNGSeed(20261108)
  ok,rv,tv,inside=cv2.solvePnPRansac(obj.astype(np.float64),uv.astype(np.float64),ki,None,iterationsCount=1500,reprojectionError=3.,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
  if not ok or inside is None or len(inside)<10:continue
  ins=inside[:,0]
  coverage=float(cv2.contourArea(cv2.convexHull(uv[ins].astype(np.float32))))/(360*640)
  spread=np.ptp(uv[ins],axis=0)
  if coverage<.025 or min(spread)<70:continue
  R=cv2.Rodrigues(rv)[0];eye=(-R.T@tv).ravel()
  if not np.isfinite(eye).all() or np.max(np.abs(eye))>25:continue
  pred=cv2.projectPoints(obj,rv,tv,ki,None)[0].reshape(-1,2);err=np.linalg.norm(pred-uv,axis=1)
  depth=(obj@R.T+tv.ravel())[:,2]
  if np.mean(depth[ins]>0)<.95:continue
  ins=ins[err[ins]<3.]
  if len(ins)<10:continue
  coverage=float(cv2.contourArea(cv2.convexHull(uv[ins].astype(np.float32))))/(360*640)
  if coverage<.025 or min(np.ptp(uv[ins],axis=0))<70:continue
  score=(len(ins),-float(np.median(err[ins])))
  if best is None or score>best[0]:best=(score,dict(K=ki.tolist(),rvec=rv.ravel().tolist(),tvec=tv.ravel().tolist(),R_world_to_camera=R.tolist(),eye=eye.tolist(),inlier_indices=ins.tolist(),inlier_count=len(ins),convex_hull_image_fraction=coverage,match_count=len(uv),median_inlier_error_px=float(np.median(err[ins])),inlier_image_bbox=np.ptp(uv[ins],axis=0).tolist(),xyz_singular_values=np.linalg.svd(obj[ins]-obj[ins].mean(0),compute_uv=False).tolist(),correspondences=[dict(uv=a.tolist(),xyz=b.tolist(),error_px=float(c)) for a,b,c in zip(uv,obj,err)]))
 return None if best is None else best[1]

def validate_candidate(path):
 """Refit from a fixed feature-level train split; reserve points from fitting."""
 from scipy.optimize import least_squares
 path=Path(path);original=json.loads(path.read_text())
 data=original['correspondences'];uv=np.array([p['uv'] for p in data]);xyz=np.array([p['xyz'] for p in data])
 rng=np.random.default_rng(20261108);indices=rng.permutation(len(uv));nval=max(5,int(len(uv)*.3));vi=indices[:nval];ti=indices[nval:]
 K=np.array([[420.,0,180.],[0,420.,320.],[0,0,1.]])
 estimate=pnp(uv[ti],xyz[ti],K)
 if estimate is None:
  report=dict(status='failed_training_subset',train_indices=ti.tolist(),validation_indices=vi.tolist(),reason='Independent split cannot establish spatially distributed PnP consensus')
 else:
  ins=ti[np.array(estimate['inlier_indices'])];ki=np.array(estimate['K'])
  initial=np.r_[estimate['rvec'],estimate['tvec'],np.log(ki[0,0])]
  def residual(p,ix):
   k=ki.copy();k[0,0]=k[1,1]=np.exp(p[6])
   pred=cv2.projectPoints(xyz[ix],p[:3],p[3:6],k,None)[0].reshape(-1,2)
   return pred-uv[ix]
  solution=least_squares(lambda p:residual(p,ins).ravel(),initial,loss='huber',f_scale=2.,max_nfev=300,bounds=([-np.inf]*6+[np.log(180)],[np.inf]*6+[np.log(900)]))
  p=solution.x;ki[0,0]=ki[1,1]=float(np.exp(p[6]));R=cv2.Rodrigues(p[:3])[0];eye=(-R.T@p[3:6]).tolist()
  errors=np.linalg.norm(residual(p,np.arange(len(uv))),axis=1)
  report=dict(status='candidate_feature_holdout_evaluated_not_GT',train_indices=ti.tolist(),validation_indices=vi.tolist(),training_consensus_indices=ins.tolist(),K=ki.tolist(),R_world_to_camera=R.tolist(),eye=eye,rvec=p[:3].tolist(),tvec=p[3:6].tolist(),validation_errors_px=errors[vi].tolist(),validation_median_error_px=float(np.median(errors[vi])),validation_under_5px_count=int(np.sum(errors[vi]<5)),train_median_error_px=float(np.median(errors[ins])),all_errors_px=errors.tolist(),validation_median_diagonal_fraction=float(np.median(errors[vi])/np.hypot(360,640)),image_resolution=[360,640],reference_id=original['reference_id'],reference_sha256=original['reference_sha256'],source_ply=original['source_ply'],assumptions='Square pixels, centered principal point, zero radial/tangential distortion; f jointly refit using train inliers only',limitations='Fixed feature-level holdout tests projection generalization, not independently surveyed 3D truth. Candidate retrieval selected using all dev-image correspondences; final view remains subject to independent actual-GS inspection.')
  canvas=cv2.resize(cv2.imread(str(BASE/'references/frames'/f"{original['reference_id']}.png")),(360,640))
  predictions=cv2.projectPoints(xyz,p[:3],p[3:6],ki,None)[0].reshape(-1,2)
  for j,(a,b) in enumerate(zip(uv,predictions)):
   if not np.isfinite(b).all() or np.max(np.abs(b))>10000:continue
   color=(0,220,0) if j in ins else ((220,0,220) if j in vi else (0,0,255))
   a=tuple(np.rint(a).astype(int));b=tuple(np.rint(b).astype(int));cv2.circle(canvas,a,4,color,1);cv2.line(canvas,a,b,color,1);cv2.putText(canvas,str(j),a,cv2.FONT_HERSHEY_SIMPLEX,.3,color,1)
  cv2.imwrite(str(path.with_name(path.stem+'_feature_validation.jpg')),canvas)
 output=path.with_name(path.stem+'_feature_validation.json');output.write_text(json.dumps(report,indent=2));print(json.dumps(report),flush=True)
 return report

def localize_from_render(render_dir,stem,out,validate_pose=None,split='dev'):
 render_dir=Path(render_dir);out=Path(out);out.mkdir(parents=True,exist_ok=True)
 camera=json.loads((render_dir/f'{stem}.camera.json').read_text())
 color=cv2.cvtColor(cv2.imread(str(render_dir/f'{stem}.png')),cv2.COLOR_BGR2RGB)
 depth=np.load(render_dir/f'{stem}.depth.npy');depth=cv2.resize(depth,(360,640),interpolation=cv2.INTER_NEAREST)
 color=cv2.resize(color,(360,640));rk,rd=feat(color)
 k=np.array(camera['K']);k[:2]*=.5
 u,v=np.meshgrid(np.arange(360),np.arange(640));rays=np.stack([(u-k[0,2])/k[0,0],(v-k[1,2])/k[1,1],np.ones_like(u)],-1)
 local=rays*np.where(np.isfinite(depth),depth,0)[:,:,None];c2w=np.array(camera['camera_to_world_cv']);world=local@c2w[:3,:3].T+c2w[:3,3]
 ids=np.arange(360*640,dtype=np.int32).reshape(640,360);ids[(~np.isfinite(depth))|(depth<=0)|(depth>100)]=-1
 manifest=json.loads((BASE/'references/manifest.json').read_text());results={}
 fixed=None if validate_pose is None else json.loads(Path(validate_pose).read_text())
 for row in manifest['frames']:
  if row['split']!=split or (fixed is not None and row['id']!=fixed['reference_id']):continue
  ref=cv2.cvtColor(cv2.imread(str(BASE/'references'/row['filename'])),cv2.COLOR_BGR2RGB);ref=cv2.resize(ref,(360,640));qk,qd=feat(ref)
  paired=matches(qk,qd,rk,rd,ids,world.reshape(-1,3))
  if paired is None:continue
  uv,obj,pointids=paired
  # Remove orientation duplicates and nearby pixels before any pose fitting.
  keep=[]
  for i in range(len(uv)):
   if not keep or min(np.linalg.norm(uv[i]-uv[keep],axis=1))>=3:keep.append(i)
  uv,obj=uv[keep],obj[keep]
  if fixed is None:
   pose=pnp(uv,obj,np.array([[420.,0,180.],[0,420.,320.],[0,0,1.]]))
   if pose is None:continue
   pose.update(reference_id=row['id'],reference_sha256=row['sha256'],split=split,source_ply=str(PLY),image_resolution=[360,640],status='candidate_from_proxy_renderer_depth_NOT_calibrated_geometry',render_camera=str(render_dir/f'{stem}.camera.json'))
   (out/f"{row['id']}.json").write_text(json.dumps(pose,indent=2));results[row['id']]={k:pose[k] for k in ('inlier_count','match_count','eye','median_inlier_error_px')}
  else:
   original=json.loads(Path(validate_pose).with_name(Path(validate_pose).name.replace('_feature_validation','')).read_text())
   old=np.array([r['uv'] for r in original['correspondences']])
   keep=np.min(np.linalg.norm(uv[:,None]-old[None],axis=2),axis=1)>8
   uv,obj=uv[keep],obj[keep]
   kk=np.array(fixed['K']);rv=np.array(fixed['rvec']);tv=np.array(fixed['tvec'])
   pred=cv2.projectPoints(obj,rv,tv,kk,None)[0].reshape(-1,2);err=np.linalg.norm(pred-uv,axis=1)
   report=dict(status='additional_correspondences_evaluated_without_refit',reference_id=row['id'],pose=str(validate_pose),fixed_pose=True,new_correspondence_count=len(uv),excluded_training_point_radius_px=8,median_error_px=None if not len(err) else float(np.median(err)),under_5px_count=int(np.sum(err<5)),under_10px_count=int(np.sum(err<10)),errors_px=err.tolist(),correspondences=[dict(uv=a.tolist(),xyz=b.tolist(),predicted_uv=c.tolist()) for a,b,c in zip(uv,obj,pred)],depth_caveat='3D points backprojected from Gaussian accumulated renderer depth, not independent ground-truth surface',image_resolution=[360,640])
   (out/f"{row['id']}_additional_validation.json").write_text(json.dumps(report,indent=2));results[row['id']]=report
 print(json.dumps({key:{k:v for k,v in value.items() if k not in ('correspondences','errors_px')} for key,value in results.items()}),flush=True)
 return results

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,default=BASE/'localization/cpu_search');ap.add_argument('--quick',action='store_true');ap.add_argument('--validate-candidate',type=Path);ap.add_argument('--render-dir',type=Path);ap.add_argument('--render-stem',default='yaw0');ap.add_argument('--fixed-pose',type=Path);ap.add_argument('--split',choices=['dev','heldout'],default='dev');args=ap.parse_args()
 if args.render_dir:
  localize_from_render(args.render_dir,args.render_stem,args.out,args.fixed_pose,args.split);return
 if args.split!='dev':
  ap.error('Heldout registration requires an unchanged baseline --render-dir; no point-cloud search or training')
 if args.validate_candidate:
  validate_candidate(args.validate_candidate);return
 args.out.mkdir(parents=True,exist_ok=True)
 manifest=json.loads((BASE/'references/manifest.json').read_text());rows=[r for r in manifest['frames'] if r['split']=='dev']
 w,h=360,640;K=np.array([[420.,0,w/2],[0,420.,h/2],[0,0,1.]])
 refs=[]
 for row in rows:
  rgb=cv2.cvtColor(cv2.imread(str(BASE/'references'/row['filename'])),cv2.COLOR_BGR2RGB);rgb=cv2.resize(rgb,(w,h));k,d=feat(rgb);refs.append((row,rgb,k,d))
 xyz,rgb=load_points(PLY);print('points',len(xyz),flush=True)
 best={};history=[]
 positions=[(x,y,1.1) for x in [-3.,-1.,1.,3.] for y in [-1.5,.5,1.8]]
 if args.quick:positions=[(0,0,1.1),(3,-1.5,1.1),(-3,.5,1.1)]
 start=time.time()
 for ci,(eye,yaw) in enumerate((eye,yaw) for eye in positions for yaw in range(0,360,30)):
  R=rotation(yaw,-8);image,ids=raster(xyz,rgb,R,np.array(eye),K,w,h);rk,rd=feat(image)
  for row,ref,k,d in refs:
   result=matches(k,d,rk,rd,ids,xyz)
   if result is None:continue
   uv,obj,pointids=result
   pose=pnp(uv,obj,K)
   if pose is None:continue
   name=row['id'];score=pose['inlier_count']
   if name not in best or score>best[name]['inlier_count']:
    pose.update(reference_id=name,reference_sha256=row['sha256'],retrieval_eye=list(eye),retrieval_yaw=yaw,point_ids=pointids.tolist(),image_resolution=[w,h],source_ply=str(PLY),status='candidate_requires_actual_GS_and_independent_review',intrinsics_assumption='unknown focal grid; shared fx=fy, centered principal point, zero distortion; resized original portrait',render_model='nearest Gaussian centers with SH DC RGB; no alpha compositing/covariance/SH direction')
    best[name]=pose
    cv2.imwrite(str(args.out/f'{name}_retrieval.jpg'),cv2.cvtColor(image,cv2.COLOR_RGB2BGR))
    (args.out/f'{name}.json').write_text(json.dumps(pose,indent=2))
    print('BEST',name,score,'of',len(uv),'eye',pose['eye'],'err',pose['median_inlier_error_px'],flush=True)
  history.append(dict(candidate=ci,eye=eye,yaw=yaw,features=0 if rd is None else len(rd),elapsed=time.time()-start))
  if ci%12==0:print('candidate',ci,'elapsed',round(time.time()-start),flush=True)
  (args.out/'progress.json').write_text(json.dumps(dict(completed=len(history),best={k:v['inlier_count'] for k,v in best.items()},elapsed_s=time.time()-start),indent=2))
 report=dict(status='cpu_retrieval_complete',gpu_used=False,dev_only=True,points=len(xyz),candidate_count=len(history),history=history,best={k:{n:v[n] for n in ('inlier_count','match_count','eye','median_inlier_error_px','status')} for k,v in best.items()},limitations=['Candidate estimates are not GT.','Nearest center renders are a retrieval approximation; reprojection agreement alone cannot prove physical pose.','Unknown phone intrinsics/distortion must be validated.'])
 (args.out/'report.json').write_text(json.dumps(report,indent=2))
 print(json.dumps(report['best']),flush=True)
if __name__=='__main__':main()
