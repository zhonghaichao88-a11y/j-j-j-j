"""第三方签名 / 验签：用自己生成的密钥做往返校验，外加阿里云官方文档里的签名示例。"""
import base64
import json
import time
import urllib.parse

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from server import providers


def test_id_number():
    assert providers.check_id_number("11010119900307851" + "5").year == 1990
    for bad in ["110101199003078514", "1101011990030785", "11010119901307851X"]:
        try:
            providers.check_id_number(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    from datetime import date
    assert providers.age_on(date(2000, 10, 10), date(2018, 10, 9)) == 17
    assert providers.age_on(date(2000, 10, 10), date(2018, 10, 10)) == 18


def test_aliyun_signature_doc_example():
    # 阿里云 RPC 签名文档里的示例（ECS DescribeRegions，AccessKeySecret = testsecret）
    params = {"AccessKeyId": "testid", "Action": "DescribeRegions", "Format": "XML", "SignatureMethod": "HMAC-SHA1",
              "SignatureNonce": "3ee8c1b8-83d3-44af-a94f-4e0ad82fd6cf", "SignatureVersion": "1.0",
              "Timestamp": "2016-02-23T12:46:24Z", "Version": "2014-05-26"}
    assert providers.aliyun_rpc_sign(params, "testsecret") == "OLeaidS1JvxuMvnyHOwuJ+uX5qY="


def test_alipay_url_and_notify(monkeypatch):
    app_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ali_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    url = providers.alipay_pay_url("ORDER1", 68.0, "SeeU 720 金币", private_key=app_key, app_id="2021000")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert q["method"] == "alipay.trade.wap.pay" and json.loads(q["biz_content"])["total_amount"] == "68.00"
    assert providers.alipay_verify(q, app_key.public_key())                   # 我们的签名能被验证
    q["biz_content"] = q["biz_content"].replace("68.00", "0.01")
    assert not providers.alipay_verify(q, app_key.public_key())               # 被篡改就验不过
    # 模拟支付宝回调：用「支付宝私钥」签名，我们用支付宝公钥验
    form = {"app_id": "2021000", "out_trade_no": "ORDER1", "total_amount": "68.00", "trade_status": "TRADE_SUCCESS",
            "notify_id": "n1", "sign_type": "RSA2"}
    form["sign"] = providers.alipay_sign(form, ali_key)
    assert providers.alipay_parse_notify(form, ali_key.public_key()) == {"order_id": "ORDER1", "yuan": 68.0}
    form["total_amount"] = "6800.00"
    assert providers.alipay_parse_notify(form, ali_key.public_key()) is None


def test_wxpay_auth_and_notify():
    mch_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hdr = providers.wxpay_auth_header("POST", "/v3/pay/transactions/h5", '{"a":1}', mch_key, "190000", "SERIAL",
                                      nonce="abc", ts=1700000000)
    sig = hdr.split('signature="')[1].split('"')[0]
    mch_key.public_key().verify(base64.b64decode(sig), b'POST\n/v3/pay/transactions/h5\n1700000000\nabc\n{"a":1}\n',
                                padding.PKCS1v15(), hashes.SHA256())
    # 模拟微信回调：APIv3 密钥加密 resource，平台私钥签名
    plat_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    apiv3 = "0123456789abcdef0123456789abcdef"
    inner = json.dumps({"out_trade_no": "ORDER2", "trade_state": "SUCCESS", "amount": {"total": 3000}}).encode()
    ct = AESGCM(apiv3.encode()).encrypt(b"nonce1234567", inner, b"transaction")
    body = json.dumps({"resource": {"algorithm": "AEAD_AES_256_GCM", "ciphertext": base64.b64encode(ct).decode(),
                                    "nonce": "nonce1234567", "associated_data": "transaction"}}).encode()
    ts = str(int(time.time()))
    sig = base64.b64encode(plat_key.sign(f"{ts}\nN\n{body.decode()}\n".encode(), padding.PKCS1v15(), hashes.SHA256())).decode()
    headers = {"Wechatpay-Timestamp": ts, "Wechatpay-Nonce": "N", "Wechatpay-Signature": sig}
    assert providers.wxpay_parse_notify(headers, body, plat_key.public_key(), apiv3) == {"order_id": "ORDER2", "yuan": 30.0}
    assert providers.wxpay_parse_notify({**headers, "Wechatpay-Nonce": "X"}, body, plat_key.public_key(), apiv3) is None
    old = {**headers, "Wechatpay-Timestamp": "1000"}
    assert providers.wxpay_parse_notify(old, body, plat_key.public_key(), apiv3) is None
