from typing import Optional
from sqlmodel import SQLModel, Field

try:
    print("Defining BadChunk...")
    class BadChunk(SQLModel):
        id: Optional[int] = Field(primary_key=True) # Removed default=None
        text: str
    
    print("BadChunk defined successfully.")
    try:
        c = BadChunk(text="hello")
        print(f"BadChunk instantiated without id: {c}")
    except Exception as e:
        print(f"Instantiation without id failed: {e}")

    c2 = BadChunk(id=1, text="hello")
    print(f"BadChunk instantiated with id: {c2}")

except Exception as e:
    print(f"Error: {e}")
