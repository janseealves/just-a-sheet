"""Listagem e download de arquivos de uma pasta do Google Drive."""

import io

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from just_a_sheet.models import ArquivoDrive
from just_a_sheet.sheet import ESCOPOS

_PREFIXO_GOOGLE_APPS = "application/vnd.google-apps."
_MIME_PASTA = "application/vnd.google-apps.folder"


class DriveGoogle:
    def __init__(self, folder_id: str, credenciais: str, archive_folder_id: str = ""):
        self._folder_id = folder_id
        self._archive_folder_id = archive_folder_id
        creds = service_account.Credentials.from_service_account_file(
            credenciais, scopes=ESCOPOS
        )
        self._servico = build("drive", "v3", credentials=creds, cache_discovery=False)

    def listar(self) -> list[ArquivoDrive]:
        arquivos: list[ArquivoDrive] = []
        token = None
        while True:
            resposta = (
                self._servico.files()
                .list(
                    q=f"'{self._folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, md5Checksum, mimeType)",
                    pageSize=1000,
                    pageToken=token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for f in resposta.get("files", []):
                if f.get("mimeType", "").startswith(_PREFIXO_GOOGLE_APPS):
                    continue  # pastas, Docs, Sheets: não são extratos
                arquivos.append(
                    ArquivoDrive(
                        id=f["id"], nome=f["name"], md5=f.get("md5Checksum", "")
                    )
                )
            token = resposta.get("nextPageToken")
            if not token:
                return arquivos

    def baixar(self, file_id: str) -> bytes:
        requisicao = self._servico.files().get_media(
            fileId=file_id, supportsAllDrives=True
        )
        buffer = io.BytesIO()
        baixador = MediaIoBaseDownload(buffer, requisicao)
        pronto = False
        while not pronto:
            _, pronto = baixador.next_chunk()
        return buffer.getvalue()

    def pasta_do_mes(self, nome: str) -> str:
        """Id da subpasta AAAA-MM em ARCHIVE_FOLDER_ID; cria se não existir.

        A criação pode falhar (conta de serviço sem cota própria): o erro sobe e o
        pipeline trata como falha de arquivamento.
        """
        if not self._archive_folder_id:
            raise RuntimeError("ARCHIVE_FOLDER_ID não configurado")
        q = (
            f"name = '{nome}' and mimeType = '{_MIME_PASTA}' "
            f"and '{self._archive_folder_id}' in parents and trashed = false"
        )
        achadas = (
            self._servico.files()
            .list(
                q=q,
                fields="files(id)",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
            .get("files", [])
        )
        if achadas:
            return achadas[0]["id"]
        criada = (
            self._servico.files()
            .create(
                body={
                    "name": nome,
                    "mimeType": _MIME_PASTA,
                    "parents": [self._archive_folder_id],
                },
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return criada["id"]

    def mover(self, file_id: str, destino_id: str) -> None:
        """Move o arquivo da pasta de entrada para a subpasta de destino (sem renomear)."""
        self._servico.files().update(
            fileId=file_id,
            addParents=destino_id,
            removeParents=self._folder_id,
            fields="id, parents",
            supportsAllDrives=True,
        ).execute()
