import os
import numpy as np
from PIL import Image
import torch
import torchvision.transforms as transforms
from torchvision.models import resnet50
from sklearn.metrics.pairwise import cosine_similarity

# Directory containing images
IMAGE_DIR = 'matchImages'

# Load ResNet50 model (pretrained, remove final layer)
print('[1/3] Loading ResNet50 model (torchvision)...')
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
model = resnet50(pretrained=True)
model = torch.nn.Sequential(*(list(model.children())[:-1]))  # Remove classification head
model.eval()
model.to(device)

# Image preprocessing
IMG_SIZE = (224, 224)
transform = transforms.Compose([
    transforms.Resize(IMG_SIZE),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])

def get_embedding(img_path):
    try:
        img = Image.open(img_path).convert('RGB')
        x = transform(img).unsqueeze(0).to(device)
        with torch.no_grad():
            emb = model(x).cpu().numpy().flatten()
        return emb
    except Exception as e:
        print(f'Failed to process {img_path}: {e}')
        return None

# List all images in directory
img_files = [os.path.join(IMAGE_DIR, f) for f in os.listdir(IMAGE_DIR)
             if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

print(f'[2/3] Extracting embeddings for {len(img_files)} images...')
embeddings = []
valid_files = []
for idx, img_path in enumerate(img_files, 1):
    emb = get_embedding(img_path)
    if emb is not None:
        embeddings.append(emb)
        valid_files.append(img_path)
    print(f'  [{idx}/{len(img_files)}] Processed: {os.path.basename(img_path)}')

embeddings = np.array(embeddings)

# Compute pairwise cosine similarity
print('[3/3] Matching images using deep learning embeddings...')
THRESHOLD = 0.86  # Adjust as needed
matches = []
for i in range(len(valid_files)):
    for j in range(i+1, len(valid_files)):
        sim = cosine_similarity([embeddings[i]], [embeddings[j]])[0][0]
        if sim >= THRESHOLD:
            matches.append((valid_files[i], valid_files[j], sim))

print(f'\nDone! {len(matches)} matches found with similarity >= {THRESHOLD*100:.0f}%.')
for f1, f2, sim in matches:
    print(f'{os.path.basename(f1)} <-> {os.path.basename(f2)} | Similarity: {sim*100:.2f}%')