import sys
from datetime import datetime,timezone
from pathlib import Path
import pytest
if not Path('/Users/kickstart/ai-management-os').exists():
    pytest.skip('AI OS integration test requires connected Mac Studio runtime',allow_module_level=True)
sys.path.insert(0,'/Users/kickstart/ai-management-os')
from ai_management_os.current_tasks import CurrentTaskStore
from ai_management_os.runtime.chat_message_delivery import ChatMessageStore
from ai_management_os.local.current_ceo_reviews import CurrentCEOReviews

def test_postpublish_audit_is_visible_without_blocking_article(tmp_path):
    tasks=CurrentTaskStore(tmp_path)
    tid='TASK-BU002-POSTPUBLISH-CEO-AUDIT-TEST01'
    tasks.create(project_id='BU-002',task_id=tid,role_id='sellemy-ops',body='Sellemy published sample audit',created_by='sellemy-ops',routine_runtime=True)
    tasks.transition(tid,'in_progress');tasks.transition(tid,'review_required')
    ms=ChatMessageStore(tmp_path/'role-communication');ms.create_chat('CHAT-TASK-'+tid,task_id=tid)
    msg=ms.send_message(message_id='CEO-AUDIT-TEST01',chat_id='CHAT-TASK-'+tid,from_role='sellemy-ops',to_role='os-chief',body='CEO review artifact_url=https://www.sellemy.jp/article/gadget/test-article.html review_points: published content evidence audit only',reply_required=True)
    ms.mark_sent(msg['message_id'],sent_at=datetime.now(timezone.utc))
    reviews=CurrentCEOReviews(tmp_path)
    items=reviews.items()
    assert len(items)==1
    assert items[0]['scope']=='BU-002'
    assert items[0]['links'][0]['url']=='https://www.sellemy.jp/article/gadget/test-article.html'
    reviews.complete(review_id=items[0]['id'],decision='差戻し',handoff='Correct product evidence')
    assert reviews.items()==[]
    # Separate CEO audit task is review_required, but Growth article job is not part of this task store.
    assert tasks.read(tid)['status']=='review_required'
