import torch

print("Torch version:", torch.__version__)
print("CUDA available:", torch.cuda.is_available())

x = torch.randn(3, 3).cuda()
y = torch.randn(3, 3).cuda()
print("Matrix sum:", (x + y))
