from sentence_transformers import SentenceTransformer

# 这一行会触发自动下载，如果缓存中没有该模型的话
model = SentenceTransformer('all-MiniLM-L6-v2')

sentences = ["这是一条测试句子。", "看看模型能否正常工作。"]
embeddings = model.encode(sentences)

print(f"成功生成向量，向量维度为: {embeddings.shape}")
# 输出应为: 成功生成向量，向量维度为: (2, 384)
