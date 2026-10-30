
from services.vector_store import AgricultureVectorStore

if __name__ == "__main__":
    store = AgricultureVectorStore()
    result = store.index_knowledge_base()
    print("\nIndexing result:")
    print(result)
