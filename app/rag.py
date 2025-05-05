import os
import logging
from langchain_community.document_loaders import DirectoryLoader, UnstructuredMarkdownLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# Configure logging (can be kept as a fallback or removed if logger is always passed)
# logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- RAG Database Initialization --- 

def initialize_rag_db(vault_path: str, persist_directory: str, logger: logging.Logger):
    """
    Initializes or loads the RAG vector database from markdown files.

    Args:
        vault_path: The path to the directory containing markdown files.
        persist_directory: The path where the Chroma database should be persisted.
        logger: The logger instance to use for logging messages.
    """
    
    # Validate vault_path
    if not os.path.isdir(vault_path):
        logger.error(f"Vault path does not exist or is not a directory: {vault_path}")
        raise FileNotFoundError(f"Vault path not found: {vault_path}")

    # Check if the database already exists and has data
    if os.path.exists(persist_directory) and os.listdir(persist_directory):
        logger.info(f"Loading existing RAG DB from: {persist_directory}")
        # Potential check: Verify if the existing DB is valid or needs update
        # For now, we assume if it exists, it's usable.
        try:
             # Attempt to load to ensure it's not corrupted (optional but good practice)
             _ = Chroma(persist_directory=persist_directory, 
                        embedding_function=HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2", model_kwargs={"device": "cpu"}))
             logger.info("Existing RAG DB loaded successfully.")
             return # DB exists and seems okay, no need to re-initialize
        except Exception as e:
             logger.warning(f"Could not load existing DB from {persist_directory}, attempting to re-initialize. Error: {e}")
             # Optionally: backup or remove the corrupted directory here
             # shutil.rmtree(persist_directory) 

    logger.info(f"Initializing new RAG DB. Source: {vault_path}, Target: {persist_directory}")

    # Ensure the persist directory exists
    os.makedirs(persist_directory, exist_ok=True)

    # Load markdown documents
    logger.info("Loading markdown documents...")
    loader = DirectoryLoader(vault_path, glob="**/*.md", loader_cls=UnstructuredMarkdownLoader, 
                             show_progress=True, use_multithreading=True)
    try:
        documents = loader.load()
        if not documents:
            logger.warning(f"No markdown documents found in {vault_path}. RAG DB will be empty.")
            # Create an empty DB structure if desired, or simply return
            # For now, we proceed, potentially creating an empty DB if Chroma allows
            # return # Or raise an error if an empty vault is invalid
    except Exception as e:
        logger.error(f"Error loading documents from {vault_path}: {e}", exc_info=True)
        raise

    logger.info(f"Loaded {len(documents)} documents.")

    # Split documents into chunks
    logger.info("Splitting documents into chunks...")
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    texts = text_splitter.split_documents(documents)
    logger.info(f"Split into {len(texts)} text chunks.")

    # Create embeddings
    logger.info("Creating embeddings (this may take a while)...")
    # Consider device management ('cuda' if GPU available, 'mps' for Apple Silicon, else 'cpu')
    # device = 'cuda' if torch.cuda.is_available() else 'mps' if torch.backends.mps.is_available() else 'cpu'
    # For simplicity and wider compatibility, using CPU for now.
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2", model_kwargs={"device": "cpu"})
    logger.info("Embeddings model loaded.")

    # Create and persist Chroma vector store
    logger.info("Creating and persisting Chroma vector store...")
    try:
        vector_store = Chroma.from_documents(
            documents=texts, 
            embedding=embeddings, 
            persist_directory=persist_directory
        )
        # Explicitly persist (though from_documents often does this)
        # vector_store.persist() # Removed - Persistence handled by persist_directory argument
        logger.info(f"Chroma vector store created and persisted at: {persist_directory}")
    except Exception as e:
        logger.error(f"Failed to create or persist Chroma vector store: {e}", exc_info=True)
        raise

# --- RAG Query Function --- 

def query_rag_db(query_text: str, db_path: str, logger: logging.Logger, n_results: int = 3) -> list:
    """
    Queries the RAG vector database.

    Args:
        query_text: The text to search for.
        db_path: The path to the persisted Chroma database.
        logger: The logger instance to use.
        n_results: The number of results to return.

    Returns:
        A list of relevant document chunks.
    """
    if not os.path.exists(db_path) or not os.listdir(db_path):
        logger.warning(f"RAG DB not found or is empty at {db_path}. Cannot query.")
        return []
    
    logger.info(f"Querying RAG DB at {db_path} for: '{query_text[:50]}...' with k={n_results}")
    try:
        # Initialize embeddings (same model as used for creation)
        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2", model_kwargs={"device": "cpu"})
        
        # Load the vector store
        vector_store = Chroma(persist_directory=db_path, embedding_function=embeddings)
        
        # Perform similarity search
        results = vector_store.similarity_search(query_text, k=n_results)
        logger.info(f"Found {len(results)} results from RAG DB.")
        return results
    except Exception as e:
        logger.error(f"Error querying RAG DB at {db_path}: {e}", exc_info=True)
        return []

# Example Usage (for testing script directly)
# if __name__ == "__main__":
#     test_vault_path = "../path/to/your/markdown/vault" # Adjust path
#     test_db_path = "../rag_db_test" # Adjust path
#     
#     # Create a logger for testing
#     test_logger = logging.getLogger('rag_test')
#     test_logger.setLevel(logging.INFO)
#     handler = logging.StreamHandler()
#     formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
#     handler.setFormatter(formatter)
#     test_logger.addHandler(handler)
#     
#     if not os.path.exists(test_vault_path):
#         test_logger.error(f"Test vault path does not exist: {test_vault_path}")
#     else:
#         try:
#             initialize_rag_db(test_vault_path, test_db_path, test_logger)
#             
#             # Test query
#             search_query = "What is project X about?" # Adjust query
#             relevant_docs = query_rag_db(search_query, test_db_path, test_logger)
#             test_logger.info(f"\nQuery: '{search_query}'")
#             if relevant_docs:
#                 for i, doc in enumerate(relevant_docs):
#                     test_logger.info(f"Result {i+1}:\n{doc.page_content[:200]}...\nSource: {doc.metadata.get('source', 'N/A')}\n---")
#             else:
#                 test_logger.info("No relevant documents found.")
#                 
#         except Exception as main_e:
#             test_logger.error(f"An error occurred during testing: {main_e}")
