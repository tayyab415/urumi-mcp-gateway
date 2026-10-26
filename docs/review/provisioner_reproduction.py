"""Reconcile a failed setup Job through the actual retry and reconcile functions."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest.mock import patch
from provisioner import cluster,state
from provisioner.app import Provisioner,StoreIn
from provisioner.config import Settings
cfg=Settings('x'*20,'example.invalid','review@example.invalid','unused','unused','unused','urumi-app','urumi-provisioner','urumi-store-tenant',2,4,900,3,5)
annotations={state.ANN_PHASE:'Failed',state.ANN_NAME:'Review failed fixture',state.ANN_CREATED:state.iso(state.now_utc()),state.ANN_DEADLINE:state.iso(state.now_utc()+timedelta(seconds=900)),state.ANN_ATTEMPTS:'1'}
ns=NS(metadata=NS(name='store-review-fixture',labels={state.LABEL_STORE:'true',state.LABEL_SLUG:'review-fixture',state.LABEL_OWNER:'4'},annotations=annotations),status=NS(phase='Active'))
operations=[]
def annotate(name,changes):annotations.update(changes);operations.append(('annotate',changes.copy()))
async def status(*args):operations.append(('helm_status','deployed'));return {'info':{'status':'deployed'}}
async def install(*args):operations.append(('helm_install','called'));return cluster.HelmResult(True,'','')
def observed(*args):return state.Observed(database_ready=True,wordpress_ready=True,mcp_ready=True,setup_failed='BackoffLimitExceeded: temporary plugin download outage')
async def main():
 p=Provisioner(cfg)
 with patch.multiple(cluster,annotate=annotate,ensure_rolebinding=lambda *a:None,ensure_secret=lambda *a:None,helm_status=status,helm_install=install,observe=observed):
  status_code,out=p._repeat_create(ns,'review-fixture',StoreIn(name='Review failed fixture',owner_id=4,owner_email='chen@urumi.test'))
  print('Retry API:',status_code,out['phase'])
  await p.reconcile(ns,'review-fixture')
  print('After actual reconcile:',annotations[state.ANN_PHASE],annotations[state.ANN_REASON])
  print('Helm install called:',any(x[0]=='helm_install' for x in operations))
  print('Operations:',operations)
asyncio.run(main())
