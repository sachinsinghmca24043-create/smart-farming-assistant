from pydantic import BaseModel, ConfigDict, Field, EmailStr, field_validator


class FarmerRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be empty.")
        return value


class FarmerLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class FarmerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    name: str
    email: EmailStr
    role: str


class AuthResponse(BaseModel):
    status: str
    message: str
    user: FarmerResponse


class AuthUserResponse(BaseModel):
    authenticated: bool
    user: FarmerResponse | None = None