payload={"conversation_id":"DxmDhLxW8DUsCnBLacySZw","request_id":"y4lilJR0u8GqQrT_","decision":"approve"}
emit('Bob approving admin held call',Client().request('/api/chat/approve','POST',payload,{'Authorization':'Bearer '+key('bob')})[:2])
emit('Bob continuing admin conversation',Client().request('/api/chat','POST',{'conversation_id':payload['conversation_id'],'message':'Continue'}, {'Authorization':'Bearer '+key('bob')})[:2])
emit('before owner approval, product lookup',rpc('admin','tools/call',{'name':'store-review-astra__list_products','arguments':{'search':'Review Practice Ball'}})[:2])
