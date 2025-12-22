from typing import Optional
from sqlmodel import SQLModel, Field

try:
    print("Defining BadChunk...")
    class BadChunk(SQLModel):
        id: Optional[int] = Field(default=None, primary_key=True)
        text: str
    
    print("BadChunk defined successfully.")
    c = BadChunk(text="hello")
    print(f"BadChunk instantiated: {c}")

except Exception as e:
    print(f"Error: {e}")
