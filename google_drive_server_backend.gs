/**
 * =========================================================================
 * SpotiLinux — Backend Serverless para Google Drive (Google Apps Script)
 * =========================================================================
 * Este script convierte la carpeta compartida de Google Drive:
 * https://drive.google.com/drive/folders/1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT
 * en una API REST de servidor completa para SpotiLinux.
 *
 * CÓMO DESPLEGAR EN 1 MINUTO (Opcional pero recomendado para hosting 100% en tu Drive):
 * 1. Ve a https://script.google.com y haz clic en "Nuevo proyecto".
 * 2. Pega todo el código de este archivo.
 * 3. Haz clic en "Implementar" -> "Nueva implementación".
 * 4. Tipo: "Aplicación web".
 * 5. Ejecutar como: "Yo (tu cuenta de Google)".
 * 6. Quién tiene acceso: "Cualquier usuario" (Anyone).
 * 7. Copia la URL de la aplicación web generada (ej. https://script.google.com/macros/s/.../exec).
 * 8. Puedes usarla como endpoint directo o SpotiLinux sincronizará automáticamente con el ID de carpeta.
 */

var ROOT_FOLDER_ID = "1EE6aiD_NRvUMf8yyXHAvF2Z3RUCjEpTT";
var MUSICA_FOLDER_ID = "1HeT9OPYwMUGYi4CVYLpHc4sJMIYmmJ9T";
var VIDEOS_FOLDER_ID = "1tKyv8TEFVGP0f-r8ohMeDj5lz3OC-bsa";
var DB_FILENAME = "spotilinux_database.json";

function doGet(e) {
  var action = (e && e.parameter && e.parameter.action) || "get_db";
  var folderId = (e && e.parameter && e.parameter.folder_id) || ROOT_FOLDER_ID;

  try {
    var folder = DriveApp.getFolderById(folderId);

    if (action === "list_music") {
      var mFolder = null;
      try {
        mFolder = DriveApp.getFolderById(MUSICA_FOLDER_ID);
      } catch (_e) {}
      if (!mFolder) {
        var mf = folder.getFoldersByName("Música");
        if (!mf.hasNext()) mf = folder.getFoldersByName("Musica");
        mFolder = mf.hasNext() ? mf.next() : folder;
      }
      var files = mFolder.getFiles();
      var list = [];
      while (files.hasNext()) {
        var f = files.next();
        var mime = f.getMimeType();
        var name = f.getName();
        if (mime.indexOf("audio/") !== -1 || name.match(/\.(mp3|flac|wav|m4a|ogg|opus)$/i)) {
          list.push({
            id: f.getId(),
            name: name,
            size: f.getSize(),
            download_url: "https://drive.google.com/uc?export=download&id=" + f.getId()
          });
        }
      }
      return jsonResponse({ status: "ok", count: list.length, files: list });
    }

    if (action === "list_videos") {
      var vFolder = null;
      try {
        vFolder = DriveApp.getFolderById(VIDEOS_FOLDER_ID);
      } catch (_e) {}
      if (!vFolder) {
        var vf = folder.getFoldersByName("videos");
        if (!vf.hasNext()) vf = folder.getFoldersByName("Videos");
        vFolder = vf.hasNext() ? vf.next() : folder;
      }
      var vFiles = vFolder.getFiles();
      var vList = [];
      while (vFiles.hasNext()) {
        var vfObj = vFiles.next();
        var vMime = vfObj.getMimeType();
        var vName = vfObj.getName();
        if (vMime.indexOf("video/") !== -1 || vName.match(/\.(mp4|webm|mkv|avi|mov)$/i)) {
          vList.push({
            id: vfObj.getId(),
            name: vName,
            size: vfObj.getSize(),
            download_url: "https://drive.google.com/uc?export=download&id=" + vfObj.getId()
          });
        }
      }
      return jsonResponse({ status: "ok", count: vList.length, files: vList });
    }

    if (action === "list_images") {
      var iFolder = null;
      var ifolders = folder.getFoldersByName("imagenes");
      if (!ifolders.hasNext()) ifolders = folder.getFoldersByName("imágenes");
      if (!ifolders.hasNext()) ifolders = folder.getFoldersByName("images");
      iFolder = ifolders.hasNext() ? ifolders.next() : folder;
      var iFiles = iFolder.getFiles();
      var iList = [];
      while (iFiles.hasNext()) {
        var ifObj = iFiles.next();
        var iMime = ifObj.getMimeType();
        var iName = ifObj.getName();
        if (iMime.indexOf("image/") !== -1 || iName.match(/\.(png|jpg|jpeg|webp|gif)$/i)) {
          iList.push({
            id: ifObj.getId(),
            name: iName,
            size: ifObj.getSize(),
            download_url: "https://drive.google.com/uc?export=download&id=" + ifObj.getId(),
            view_url: "https://drive.usercontent.google.com/download?id=" + ifObj.getId() + "&export=download&confirm=t"
          });
        }
      }
      return jsonResponse({ status: "ok", count: iList.length, files: iList });
    }

    if (action === "get_temp_session") {
      var emailParam = String(e && e.parameter && e.parameter.email || "").toLowerCase().trim();
      var sessionFileName = emailParam ? ("maomao_temp_session_" + Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, emailParam).map(function(b){return (b<0?b+256:b).toString(16)}).join("") + ".json") : "maomao_temp_session.json";
      var sessionFiles = folder.getFilesByName(sessionFileName);
      if (!sessionFiles.hasNext() && emailParam) {
        sessionFiles = folder.getFilesByName("maomao_temp_session.json");
      }
      if (sessionFiles.hasNext()) {
        var sFile = sessionFiles.next();
        var sContent = sFile.getBlob().getDataAsString("UTF-8");
        try {
          var sData = JSON.parse(sContent);
          if (sData.expiresAtMs && sData.expiresAtMs < new Date().getTime()) {
            try { sFile.setTrashed(true); } catch(_delErr){}
            return jsonResponse({ status: "expired" });
          }
          return ContentService.createTextOutput(sContent).setMimeType(ContentService.MimeType.JSON);
        } catch(_parseErr) {}
      }
      return jsonResponse({ status: "not_found" });
    }

    // Por defecto: descargar la base de datos de cuentas y playlists
    var files = folder.getFilesByName(DB_FILENAME);
    if (!files.hasNext()) {
      files = folder.getFilesByName("spotilinux_cloud_playlists.json");
    }
    if (files.hasNext()) {
      var file = files.next();
      var content = file.getBlob().getDataAsString("UTF-8");
      return ContentService.createTextOutput(content).setMimeType(ContentService.MimeType.JSON);
    }

    return jsonResponse({
      app: "SpotiLinux",
      version: "2.0.0",
      root_folder_id: folderId,
      accounts: {},
      drive_catalog: []
    });
  } catch (err) {
    return jsonResponse({ error: err.toString() });
  }
}

function doPost(e) {
  try {
    var data = JSON.parse(e.postData.contents);
    var folderId = data.folder_id || data.root_folder_id || ROOT_FOLDER_ID;
    var folder = DriveApp.getFolderById(folderId);

    // 1. Acción: Subir archivo multimedia (Audio o Video) directamente sin OAuth
    if (data.action === "upload_file") {
      var targetFolderId = data.folder_type === "video" ? VIDEOS_FOLDER_ID : MUSICA_FOLDER_ID;
      if (data.folder_id) targetFolderId = data.folder_id;

      var targetFolder = DriveApp.getFolderById(targetFolderId);
      var filename = String(data.filename || "unnamed_media").trim();
      var mimeType = data.mime_type || (data.folder_type === "video" ? "video/mp4" : "audio/mpeg");

      // Evitar duplicados innecesarios si ya existe
      var existing = targetFolder.getFilesByName(filename);
      if (existing.hasNext()) {
        var existingFile = existing.next();
        return jsonResponse({
          status: "ok",
          action: "existing",
          file_id: existingFile.getId(),
          name: existingFile.getName(),
          size: existingFile.getSize(),
          download_url: "https://drive.google.com/uc?export=download&id=" + existingFile.getId()
        });
      }

      var decodedBytes = Utilities.base64Decode(data.file_data);
      var blob = Utilities.newBlob(decodedBytes, mimeType, filename);
      var createdFile = targetFolder.createFile(blob);
      try {
        createdFile.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
      } catch (_shareErr) {}

      return jsonResponse({
        status: "ok",
        action: "created",
        file_id: createdFile.getId(),
        name: createdFile.getName(),
        size: createdFile.getSize(),
        download_url: "https://drive.google.com/uc?export=download&id=" + createdFile.getId()
      });
    }

    // 2. Acción: Guardar o combinar base de datos completa
    if (data.action === "sync_db" || data.accounts) {
      var dbPayload = data.db || data;
      var files = folder.getFilesByName(DB_FILENAME);
      var file = null;
      var existingDb = { accounts: {}, drive_catalog: [] };

      if (files.hasNext()) {
        file = files.next();
        try {
          existingDb = JSON.parse(file.getBlob().getDataAsString("UTF-8")) || {};
        } catch (_e) {}
      } else {
        file = folder.createFile(DB_FILENAME, "{}", MimeType.PLAIN_TEXT);
      }

      // Combinar cuentas
      var incomingAccounts = dbPayload.accounts || {};
      var baseAccounts = existingDb.accounts || {};
      for (var accKey in incomingAccounts) {
        baseAccounts[accKey] = incomingAccounts[accKey];
      }
      existingDb.accounts = baseAccounts;
      existingDb.updated_at = new Date().getTime();
      existingDb.root_folder_id = folderId;

      file.setContent(JSON.stringify(existingDb, null, 2));
      return jsonResponse({ status: "ok", message: "Base de datos actualizada con éxito en Google Drive" });
    }

    // 2. Acción: Guardar cuenta individual
    if (data.action === "save_account") {
      var email = String(data.email || (data.account && data.account.email) || "").toLowerCase().trim();
      var files = folder.getFilesByName(DB_FILENAME);
      var file = files.hasNext() ? files.next() : folder.createFile(DB_FILENAME, "{}", MimeType.PLAIN_TEXT);
      var currentDb = { accounts: {} };
      try {
        currentDb = JSON.parse(file.getBlob().getDataAsString("UTF-8")) || { accounts: {} };
      } catch (_e) {}

      if (!currentDb.accounts) currentDb.accounts = {};
      currentDb.accounts[email] = data.account;
      currentDb.updated_at = new Date().getTime();

      file.setContent(JSON.stringify(currentDb, null, 2));
      return jsonResponse({ status: "ok", email: email });
    }

    // 4. Acción: Guardar archivo temporal de reproducción (Continuar en otro dispositivo)
    if (data.action === "save_temp_session") {
      var emailParam = String(data.email || "").toLowerCase().trim();
      var sessionFileName = emailParam ? ("maomao_temp_session_" + Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, emailParam).map(function(b){return (b<0?b+256:b).toString(16)}).join("") + ".json") : "maomao_temp_session.json";
      var sessionFiles = folder.getFilesByName(sessionFileName);
      var sFile = sessionFiles.hasNext() ? sessionFiles.next() : folder.createFile(sessionFileName, "{}", MimeType.PLAIN_TEXT);
      sFile.setContent(JSON.stringify(data.session || data, null, 2));
      return jsonResponse({ status: "ok", action: "saved", filename: sessionFileName });
    }

    // 5. Acción: Eliminar archivo temporal de reproducción
    if (data.action === "delete_temp_session") {
      var emailParam = String(data.email || "").toLowerCase().trim();
      var sessionFileName = emailParam ? ("maomao_temp_session_" + Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, emailParam).map(function(b){return (b<0?b+256:b).toString(16)}).join("") + ".json") : "maomao_temp_session.json";
      var sessionFiles = folder.getFilesByName(sessionFileName);
      while (sessionFiles.hasNext()) {
        var sf = sessionFiles.next();
        try { sf.setTrashed(true); } catch(_e){}
      }
      return jsonResponse({ status: "ok", action: "deleted" });
    }

    return jsonResponse({ error: "Acción no reconocida" });
  } catch (err) {
    return jsonResponse({ error: err.toString() });
  }
}

function jsonResponse(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
