"""Structured extraction of meeting data with the local LLM."""

from typing import Literal

import ollama
from pydantic import BaseModel, Field

# Bump when the prompt or schema changes; documents extracted with an older version are re-extracted.
EXTRACTOR_VERSION = "2"

MeetingType = Literal["board", "spring_general", "autumn_general", "extraordinary", "other"]


class Person(BaseModel):
    name: str = Field(description="Koko nimi kuten dokumentissa")
    role: str | None = Field(default=None, description="Rooli kokouksessa, esim. puheenjohtaja, sihteeri")


class Topic(BaseModel):
    # No separate item-number field: asking for one made the model emit filler forever. The number is
    # split from the title in code instead (normalize.split_item_number).
    title: str = Field(description="Asiakohdan otsikko numeroineen täsmälleen kuten dokumentissa")
    summary: str = Field(description="1-2 lauseen kuvaus siitä, mitä käsiteltiin")
    decisions: str | None = Field(
        default=None,
        description="Kokouksen tekemät päätökset tästä asiasta lyhyesti; null vain jos mitään ei päätetty",
    )


class Meeting(BaseModel):
    title: str = Field(
        description="Kokouksen nimi ilman yhdistyksen nimeä, "
        "esim. 'Hallituksen kokous 3/2021' tai 'Syyskokous 2019'"
    )
    meeting_type: MeetingType = Field(
        description="board = hallituksen kokous, spring_general = kevätkokous, "
        "autumn_general = syyskokous, extraordinary = ylimääräinen kokous, other = muu"
    )
    date: str | None = Field(default=None, description="Kokouksen päivämäärä muodossa YYYY-MM-DD")
    start_time: str | None = Field(default=None, description="Alkamisaika muodossa HH:MM")
    end_time: str | None = Field(default=None, description="Päättymisaika muodossa HH:MM")
    location: str | None = None
    present: list[Person]
    absent: list[Person]
    topics: list[Topic]
    summary: str = Field(description="3-5 lauseen yhteenveto koko kokouksesta")


SYSTEM_PROMPT = """Olet avustaja, joka poimii tietoja suomenkielisistä yhdistyksen kokouspöytäkirjoista.
Palauta vain dokumentissa oleva tieto. Älä keksi mitään. Jos tietoa ei ole, käytä null tai tyhjää listaa.
Päivämäärät ovat suomalaisessa muodossa (pp.kk.vvvv); muunna ne muotoon YYYY-MM-DD.
Ota mukaan kaikki käsitellyt asiakohdat järjestyksessä, myös muodolliset (avaus, esityslista jne.).
Kopioi asiakohtien otsikot sellaisinaan.
Kirjaa jokaisen asiakohdan päätökset kenttään decisions. Päätöksiä ovat esimerkiksi hyväksymiset,
hankinnat, rahasummat ja toimeksiannot ("Hyväksyttiin", "Hankitaan", "Päätettiin", "tuetaan 500 eurolla").
Pelkkä keskustelu ilman päätöstä ei ole päätös.
Ilmoita rooli vain, jos se mainitaan dokumentissa.
Tekstissä merkinnät [sivu N] kertovat sivunvaihdot; ne eivät ole osa sisältöä.
Kirjoita tiivistelmät suomeksi."""

# ~3.5 characters per token for Finnish; leave room for the prompt and the JSON answer.
CHARS_PER_TOKEN = 3.5
# A normal answer is 1-3k tokens. The cap turns a runaway generation into an error instead of a hang.
MAX_OUTPUT_TOKENS = 8192
TIMEOUT_SECONDS = 15 * 60
MIN_CONTEXT = 8192
MAX_CONTEXT = 65536


class DocumentTooLong(Exception):
    pass


class RunawayGeneration(Exception):
    pass


def prompt_text(pages: list[str]) -> str:
    return "\n\n".join(f"[sivu {i}]\n{text.strip()}" for i, text in enumerate(pages, 1))


def context_size(text: str) -> int:
    needed = int(len(text) / CHARS_PER_TOKEN) + 4096
    if needed > MAX_CONTEXT:
        raise DocumentTooLong(f"needs ~{needed} tokens of context, limit is {MAX_CONTEXT}")
    return max(MIN_CONTEXT, needed)


class Extractor:
    def __init__(self, host: str, model: str) -> None:
        self.client = ollama.Client(host=host, timeout=TIMEOUT_SECONDS)
        self.model = model

    def __call__(self, pages: list[str]) -> Meeting:
        text = prompt_text(pages)
        response = self.client.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Pöytäkirja:\n\n{text}"},
            ],
            format=Meeting.model_json_schema(),
            think=False,
            options={"temperature": 0, "num_ctx": context_size(text), "num_predict": MAX_OUTPUT_TOKENS},
        )
        if response.done_reason == "length":
            raise RunawayGeneration(f"the model hit the {MAX_OUTPUT_TOKENS}-token output limit")
        return Meeting.model_validate_json(response.message.content or "")
