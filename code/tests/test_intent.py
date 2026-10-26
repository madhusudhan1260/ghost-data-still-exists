import pytest

from ghost_audit.intent import authorizes_deletion

DELETE = [
    "Please erase the account linked to this email address.",
    "Solicito la eliminación permanente de la cuenta asociada a este correo.",
    "Je demande la suppression définitive du compte associé à cette adresse e-mail.",
    "Solicito a exclusão permanente da conta associada a este e-mail.",
    "このメールアドレスに関連するアカウントの完全な削除を依頼します。",
    "이 이메일 주소에 연결된 계정의 영구 삭제를 요청합니다.",
    "I closed my account, but an old “download your data” link still works. Please delete that export file as well.",
    "I deleted this account but please remove any remaining copy of my profile.",
]
NOT_DELETE = [
    "Please stop the promotional emails. I still need the account and my order history, so I am not asking for deletion.",
    "Could you send me a copy of the information held on my account? I still use it, so please don't delete or change anything.",
    "My surname is misspelled on the account. Can you correct it without removing any of my records?",
    "I can't access my profile and need help resetting the login. Do not close the account.",
    "Please remove me from marketing lists only. I still want receipts and service messages for this account.",
]


@pytest.mark.parametrize("text", DELETE)
def test_deletion_requests_are_authorized(text):
    assert authorizes_deletion(text)[0] is True


@pytest.mark.parametrize("text", NOT_DELETE)
def test_non_deletion_requests_are_not_applicable(text):
    assert authorizes_deletion(text)[0] is False
