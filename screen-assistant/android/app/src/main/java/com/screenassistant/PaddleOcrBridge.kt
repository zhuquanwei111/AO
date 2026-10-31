package com.screenassistant

import android.content.Context
import android.graphics.Bitmap
import com.paddle.ocr.PaddleOCR
import com.paddle.ocr.model.OCRRunResult
import com.paddle.ocr.util.OpenCVUtils
import kotlinx.coroutines.runBlocking

/** 在现有采集工作线程中桥接 Paddle 协程 SDK；推理和模型加载不运行在 UI 线程。 */
internal class PaddleOcrBridge(context: Context) : AutoCloseable {
    private val appContext = context.applicationContext
    @Volatile private var engine: PaddleOCR? = null

    @Synchronized
    fun initializeBlocking(): Long {
        if (engine != null) return engine!!.coldLoadTimeMs
        if (!OpenCVUtils.init(appContext)) throw IllegalStateException("OpenCV native 库初始化失败")
        val loaded = runBlocking { PaddleOCR.create(appContext) }
        engine = loaded
        return loaded.coldLoadTimeMs
    }

    @Synchronized
    fun recognizeBlocking(bitmap: Bitmap): OCRRunResult {
        if (engine == null) initializeBlocking()
        val current = engine ?: throw IllegalStateException("Paddle OCR 初始化失败")
        return runBlocking { current.recognize(bitmap) }
    }

    @Synchronized
    override fun close() {
        val current = engine ?: return
        engine = null
        runBlocking { current.release() }
    }
}
