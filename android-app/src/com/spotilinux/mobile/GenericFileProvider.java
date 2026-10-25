package com.spotilinux.mobile;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;

import java.io.File;
import java.io.FileNotFoundException;

/**
 * Proveedor de contenido seguro para compartir el archivo APK descargado
 * con el instalador de paquetes del sistema Android (Android 7.0+ Nougat a Android 14+).
 */
public class GenericFileProvider extends ContentProvider {
    public static final String AUTHORITY = "com.spotilinux.mobile.provider";

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        return null;
    }

    @Override
    public String getType(Uri uri) {
        return "application/vnd.android.package-archive";
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (getContext() == null) {
            throw new FileNotFoundException("Contexto nulo");
        }
        String fileName = uri.getLastPathSegment();
        if (fileName == null || fileName.isEmpty()) {
            fileName = "update.apk";
        }

        File file = new File(getContext().getExternalCacheDir(), fileName);
        if (!file.exists()) {
            file = new File(getContext().getCacheDir(), fileName);
        }
        if (!file.exists()) {
            throw new FileNotFoundException("Archivo no encontrado: " + uri.getPath());
        }
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY);
    }
}
