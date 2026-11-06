"""违规样例：核心层引入 §8 技术栈登记表之外的第三方预测库。

违反 SPEC §3.3 S9：apps/ 下只能出现 §8 登记在册的第三方包；
要引入新库必须先改 §8 登记表、再改检查器白名单，不得靠“大家都这么写”放行。
"""
import sklearn


def predict(rows):
    return sklearn.linear_model.LinearRegression().fit(rows, [0] * len(rows))
