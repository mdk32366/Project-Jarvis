from pydantic import BaseModel

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class LoginRequest(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    id: int
    username: str
    model_config = {"from_attributes": True}

class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str

class ChatRequest(BaseModel):
    message: str
    thread_key: str = "web"

class ChatResponse(BaseModel):
    reply: str

class MemoryIn(BaseModel):
    content: str
    category: str = "general"
    sensitive: bool = False

class MemoryOut(BaseModel):
    id: int
    category: str
    content: str
    source: str
    sensitive: bool
    model_config = {"from_attributes": True}

class PersonaOut(BaseModel):
    id: int
    category: str
    content: str
    model_config = {"from_attributes": True}

class PreferenceOut(BaseModel):
    id: int
    key: str
    value: str
    model_config = {"from_attributes": True}

class ConversationOut(BaseModel):
    id: int
    channel: str
    thread_key: str
    subject: str
    model_config = {"from_attributes": True}

class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    model_config = {"from_attributes": True}

class JobOut(BaseModel):
    id: int
    kind: str
    status: str
    attempts: int
    result: str
    error: str
    channel: str
    thread_key: str
    model_config = {"from_attributes": True}

class AgentIn(BaseModel):
    name: str
    description: str = ""
    system_prompt: str = ""
    tools: list[str] = []
    enabled: bool = True

class AgentOut(BaseModel):
    id: int
    name: str
    description: str
    system_prompt: str
    tools: list[str]
    enabled: bool

class AuditOut(BaseModel):
    id: int
    channel: str
    actor: str
    tool: str
    arguments: str
    result: str
    status: str
    model_config = {"from_attributes": True}

class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str
