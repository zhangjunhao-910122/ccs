"""违规样例三：明文凭据。

违反 SPEC §6.1：凭据只以 ENV: / VAULT: / FILE: 三种引用形式出现，
明文密钥与连接串一律判事故。
"""
password = "Sup3rSecretValue"
