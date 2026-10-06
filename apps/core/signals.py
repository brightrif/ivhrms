from django.dispatch import Signal

# Sent after commit: approval_completed.send(sender=ApprovalRequest, approval=<ApprovalRequest>)
approval_completed = Signal()