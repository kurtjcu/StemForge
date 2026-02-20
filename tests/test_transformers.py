from transformers import AutoTokenizer, AutoModel

tok = AutoTokenizer.from_pretrained("bert-base-uncased")
model = AutoModel.from_pretrained("bert-base-uncased")

out = model(**tok("hello world", return_tensors="pt"))
print("Transformers output:", out.last_hidden_state.shape)
