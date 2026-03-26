"""
DevVault — Backend FastAPI
API pour gérer scripts, notes, screenshots, fichiers
Connecté à MongoDB Atlas
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
import os
import uvicorn

# ============================================================
#  CONFIG — mettez vos vraies valeurs dans .env
# ============================================================
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME   = os.environ.get("DB_NAME",   "dash")

# ============================================================
#  APP
# ============================================================
app = FastAPI(title="DevVault API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # En prod : mettez votre domaine
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#  DATABASE
# ============================================================
client = None
db     = None

@app.on_event("startup")
async def startup():
    global client, db
    client = AsyncIOMotorClient(MONGO_URL)
    db     = client[DB_NAME]
    # Index pour la recherche
    await db.items.create_index([("name", "text"), ("content", "text"), ("tags", "text")])
    print(f"✅ Connecté à MongoDB : {DB_NAME}")

@app.on_event("shutdown")
async def shutdown():
    client.close()

# ============================================================
#  MODELS
# ============================================================
class Item(BaseModel):
    id:          str
    type:        str          # script | note | screenshot | file | snippet
    name:        str
    content:     Optional[str] = ""
    file_data:   Optional[str] = None   # base64 pour les images
    description: Optional[str] = ""
    folder_id:   Optional[str] = None
    lang:        Optional[str] = ""
    tags:        Optional[List[str]] = []
    created_at:  Optional[str] = None

class Folder(BaseModel):
    id:    str
    name:  str
    emoji: Optional[str] = "📁"
    color: Optional[str] = "#58a6ff"

class SearchQuery(BaseModel):
    query: str

# ============================================================
#  ITEMS ROUTES
# ============================================================
@app.get("/items")
async def get_items(folder_id: str = None, type: str = None):
    """Récupère tous les items, avec filtres optionnels"""
    query = {}
    if folder_id: query["folder_id"] = folder_id
    if type:      query["type"]      = type
    items = await db.items.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items

@app.get("/items/{item_id}")
async def get_item(item_id: str):
    """Récupère un item par ID"""
    item = await db.items.find_one({"id": item_id}, {"_id": 0})
    if not item:
        raise HTTPException(status_code=404, detail="Item introuvable")
    return item

@app.post("/items")
async def create_item(item: Item):
    """Crée un nouvel item"""
    data = item.dict()
    if not data.get("created_at"):
        data["created_at"] = datetime.now(timezone.utc).isoformat()
    existing = await db.items.find_one({"id": item.id})
    if existing:
        await db.items.update_one({"id": item.id}, {"$set": data})
        return {"message": "Mis à jour", "id": item.id}
    await db.items.insert_one(data)
    return {"message": "Créé", "id": item.id}

@app.put("/items/{item_id}")
async def update_item(item_id: str, item: Item):
    """Met à jour un item"""
    data = item.dict()
    result = await db.items.update_one({"id": item_id}, {"$set": data})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Item introuvable")
    return {"message": "Mis à jour"}

@app.delete("/items/{item_id}")
async def delete_item(item_id: str):
    """Supprime un item"""
    result = await db.items.delete_one({"id": item_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Item introuvable")
    return {"message": "Supprimé"}

# ============================================================
#  SEARCH
# ============================================================
@app.get("/search")
async def search_items(q: str):
    """Recherche full-text dans les items"""
    if not q.strip():
        return []
    results = await db.items.find(
        {"$text": {"$search": q}},
        {"_id": 0, "score": {"$meta": "textScore"}}
    ).sort([("score", {"$meta": "textScore"})]).to_list(50)

    # Fallback regex si pas de résultats text index
    if not results:
        regex = {"$regex": q, "$options": "i"}
        results = await db.items.find(
            {"$or": [{"name": regex}, {"content": regex}, {"tags": regex}]},
            {"_id": 0}
        ).to_list(50)
    return results

# ============================================================
#  FOLDERS ROUTES
# ============================================================
@app.get("/folders")
async def get_folders():
    """Récupère tous les dossiers"""
    folders = await db.folders.find({}, {"_id": 0}).to_list(100)
    return folders

@app.post("/folders")
async def create_folder(folder: Folder):
    """Crée un nouveau dossier"""
    data = folder.dict()
    existing = await db.folders.find_one({"id": folder.id})
    if existing:
        raise HTTPException(status_code=400, detail="Dossier déjà existant")
    await db.folders.insert_one(data)
    return {"message": "Dossier créé", "id": folder.id}

@app.put("/folders/{folder_id}")
async def update_folder(folder_id: str, folder: Folder):
    """Met à jour un dossier"""
    result = await db.folders.update_one(
        {"id": folder_id}, {"$set": folder.dict()}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Dossier introuvable")
    return {"message": "Mis à jour"}

@app.delete("/folders/{folder_id}")
async def delete_folder(folder_id: str):
    """Supprime un dossier et tous ses items"""
    await db.folders.delete_one({"id": folder_id})
    await db.items.delete_many({"folder_id": folder_id})
    return {"message": "Dossier supprimé"}

# ============================================================
#  STATS
# ============================================================
@app.get("/stats")
async def get_stats():
    """Statistiques globales"""
    total      = await db.items.count_documents({})
    scripts    = await db.items.count_documents({"type": {"$in": ["script","snippet"]}})
    notes      = await db.items.count_documents({"type": "note"})
    screenshots= await db.items.count_documents({"type": "screenshot"})
    files      = await db.items.count_documents({"type": "file"})
    folders    = await db.folders.count_documents({})
    return {
        "total": total, "scripts": scripts, "notes": notes,
        "screenshots": screenshots, "files": files, "folders": folders
    }

# ============================================================
#  HEALTH CHECK
# ============================================================
@app.get("/health")
async def health():
    return {"status": "ok", "db": DB_NAME}

# ============================================================
#  RUN
# ============================================================
if __name__ == "__main__":
    uvicorn.run("devvault_backend:app", host="0.0.0.0", port=8000, reload=True)
