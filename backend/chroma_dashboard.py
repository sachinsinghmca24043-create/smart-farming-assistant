
import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from sklearn.decomposition import PCA
from services.knowledge_service import _get_pdf_store


st.set_page_config(
    page_title="Smart Farming ChromaDB Explorer",
    page_icon="🌾",
    layout="wide"
)

st.title("🌾 Smart Farming - ChromaDB Explorer")
st.caption("Live inspection of your agricultural vector database")


@st.cache_resource
def load_store():
    return _get_pdf_store()


try:
    store = load_store()
    collection = store.collection

    total = collection.count()

    st.success("Connected to ChromaDB successfully")

    # Database overview
    st.subheader("1. Collection Overview")

    c1, c2, c3 = st.columns(3)

    c1.metric("Collection", collection.name)
    c2.metric("Total PDF chunks", total)
    c3.metric("Embedding dimensions", "384")

    st.divider()

    # Get stored records and vectors
    data = collection.get(
        include=["documents", "metadatas", "embeddings"]
    )

    embeddings = np.asarray(
        data["embeddings"], dtype=np.float32
    )

    documents = data["documents"] or []
    metadatas = data["metadatas"] or []
    ids = data["ids"]

    st.subheader("2. Inspect Stored Vectors")

    if len(embeddings):
        selected = st.number_input(
            "Select vector index",
            min_value=0,
            max_value=len(embeddings) - 1,
            value=0
        )

        st.write("Document ID:", ids[int(selected)])
        st.write("Vector shape:", embeddings[int(selected)].shape)

        st.write(
            "First 20 vector values:",
            embeddings[int(selected)][:20]
        )

        with st.expander("View original document and metadata"):
            st.write(documents[int(selected)])
            st.json(metadatas[int(selected)] or {})

    st.divider()

    # PCA visualization
    st.subheader("3. Vector Visualization (PCA)")

    if len(embeddings) >= 3:
        pca = PCA(n_components=2)
        reduced = pca.fit_transform(embeddings)

        crop_labels = [
            (m or {}).get("crop", "unknown")
            for m in metadatas
        ]

        fig, ax = plt.subplots(figsize=(10, 6))

        for crop in sorted(set(crop_labels)):
            indexes = [
                i for i, value in enumerate(crop_labels)
                if value == crop
            ]

            ax.scatter(
                reduced[indexes, 0],
                reduced[indexes, 1],
                label=crop,
                alpha=0.65
            )

        ax.set_title("Agricultural Document Embeddings")
        ax.set_xlabel("PCA Component 1")
        ax.set_ylabel("PCA Component 2")
        ax.legend(title="Crop")

        st.pyplot(fig)
        plt.close(fig)

        st.caption(
            "PCA projects the original 384-dimensional vectors "
            "into two dimensions for visualization."
        )

    st.divider()

    # Interactive semantic search
    st.subheader("4. Perform Semantic Search")

    query = st.text_input(
        "Enter a farmer's question",
        "How should I manage irrigation in rice cultivation?"
    )

    crop = st.selectbox(
        "Filter by crop",
        ["All", "rice", "wheat"]
    )

    state = st.text_input(
        "Filter by state (optional)",
        "Uttar Pradesh"
    )

    top_k = st.slider(
        "Number of results",
        min_value=1,
        max_value=10,
        value=5
    )

    if st.button("Search ChromaDB", type="primary"):
        with st.spinner("Searching vector database..."):

            results = store.search(
                query=query,
                top_k=top_k,
                crop=None if crop == "All" else crop,
                state=state.strip() or None
            )

        st.write("Retrieved records:", len(results))

        for i, result in enumerate(results, 1):
            metadata = result.get("metadata") or {}

            with st.expander(
                f"Result {i} - {result.get('id', 'Unknown ID')}",
                expanded=True
            ):
                st.write("Document:", result.get("document"))
                st.json(metadata)
                st.write("Distance:", result.get("_distance"))
                st.write("Source type:", result.get("_source_type"))

    st.divider()

    # Metadata table
    st.subheader("5. Explore Database Records")

    rows = []

    for i in range(len(ids)):
        meta = metadatas[i] or {}

        rows.append({
            "ID": ids[i],
            "Crop": meta.get("crop"),
            "State": meta.get("state"),
            "Language": meta.get("language"),
            "Verified": meta.get("verified"),
            "Document": (documents[i] or "")[:150]
        })

    df = pd.DataFrame(rows)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True
    )

except Exception as e:
    st.error(f"Dashboard error: {e}")
    st.exception(e)
