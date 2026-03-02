package com.example.hans

import android.opengl.GLES11Ext
import android.opengl.GLES20
import android.util.Log
import com.google.ar.core.Coordinates2d
import com.google.ar.core.Frame
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.FloatBuffer

class CameraRenderer {

    var textureId: Int = -1
        private set

    private var programId = -1
    private var positionAttrib = -1
    private var texCoordAttrib = -1
    private var textureUniform = -1

    // Vertex shader: Maps screen corners and passes UVs to fragment shader
    private val vertexShaderCode = """
        attribute vec4 a_Position;
        attribute vec2 a_TexCoord;
        varying vec2 v_TexCoord;
        void main() {
           gl_Position = a_Position;
           v_TexCoord = a_TexCoord;
        }
    """.trimIndent()

    // Fragment shader: Converts hardware OES (YUV) to RGB pixels
    private val fragmentShaderCode = """
        #extension GL_OES_EGL_image_external : require
        precision mediump float;
        varying vec2 v_TexCoord;
        uniform samplerExternalOES u_Texture;
        void main() {
            gl_FragColor = texture2D(u_Texture, v_TexCoord);
        }
    """.trimIndent()

    // 2D Quad representing the full screen (X, Y)
    private val quadCoords = floatArrayOf(
        -1.0f, -1.0f,
        -1.0f,  1.0f,
        1.0f, -1.0f,
        1.0f,  1.0f
    )

    private val quadBuffer: FloatBuffer = ByteBuffer.allocateDirect(quadCoords.size * 4)
        .order(ByteOrder.nativeOrder())
        .asFloatBuffer()
        .apply {
            put(quadCoords)
            position(0)
        }

    private val texBuffer: FloatBuffer = ByteBuffer.allocateDirect(8 * 4)
        .order(ByteOrder.nativeOrder())
        .asFloatBuffer()

    fun createOnGlThread() {
        // 1. Generate an OES Texture ID
        val textures = IntArray(1)
        GLES20.glGenTextures(1, textures, 0)
        textureId = textures[0]

        // 2. Bind and configure it
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, textureId)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_NEAREST)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_S, GLES20.GL_CLAMP_TO_EDGE)
        GLES20.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_WRAP_T, GLES20.GL_CLAMP_TO_EDGE)

        // 3. Compile Shaders
        val vertexShader = loadShader(GLES20.GL_VERTEX_SHADER, vertexShaderCode)
        val fragmentShader = loadShader(GLES20.GL_FRAGMENT_SHADER, fragmentShaderCode)

        // 4. Link Program
        programId = GLES20.glCreateProgram()
        GLES20.glAttachShader(programId, vertexShader)
        GLES20.glAttachShader(programId, fragmentShader)
        GLES20.glLinkProgram(programId)

        // 5. Get variable handles from the compiled shader
        positionAttrib = GLES20.glGetAttribLocation(programId, "a_Position")
        texCoordAttrib = GLES20.glGetAttribLocation(programId, "a_TexCoord")
        textureUniform = GLES20.glGetUniformLocation(programId, "u_Texture") // CRITICAL: Was missing before
    }

    fun draw(frame: Frame) {
        if (textureId == -1) return

        // Update UV coordinates if screen orientation or size changes
        if (frame.hasDisplayGeometryChanged()) {
            val transformedUvs = FloatArray(8)
            // Ask ARCore to map the physical camera sensor to the phone screen
            frame.transformCoordinates2d(
                Coordinates2d.OPENGL_NORMALIZED_DEVICE_COORDINATES,
                floatArrayOf(
                    0.0f, 0.0f,
                    0.0f, 1.0f,
                    1.0f, 0.0f,
                    1.0f, 1.0f
                ),
                Coordinates2d.TEXTURE_NORMALIZED,
                transformedUvs
            )
            texBuffer.clear()
            texBuffer.put(transformedUvs)
            texBuffer.position(0)
        }

        // --- Render the Texture ---

        // Disable Depth Test for the background video (we want it at the absolute back)
        GLES20.glDisable(GLES20.GL_DEPTH_TEST)
        GLES20.glDepthMask(false)

        // Use our compiled shader
        GLES20.glUseProgram(programId)

        // Bind the camera texture to OpenGL Texture Unit 0
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, textureId)

        // Tell the shader to use Texture Unit 0
        GLES20.glUniform1i(textureUniform, 0)

        // Pass the screen quad positions
        GLES20.glEnableVertexAttribArray(positionAttrib)
        GLES20.glVertexAttribPointer(positionAttrib, 2, GLES20.GL_FLOAT, false, 0, quadBuffer)

        // Pass the calculated UV texture coordinates
        GLES20.glEnableVertexAttribArray(texCoordAttrib)
        GLES20.glVertexAttribPointer(texCoordAttrib, 2, GLES20.GL_FLOAT, false, 0, texBuffer)

        // DRAW
        GLES20.glDrawArrays(GLES20.GL_TRIANGLE_STRIP, 0, 4)

        // Cleanup state
        GLES20.glDisableVertexAttribArray(positionAttrib)
        GLES20.glDisableVertexAttribArray(texCoordAttrib)
        GLES20.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES, 0)

        GLES20.glDepthMask(true)
        GLES20.glEnable(GLES20.GL_DEPTH_TEST)
    }

    private fun loadShader(type: Int, shaderCode: String): Int {
        val shader = GLES20.glCreateShader(type)
        GLES20.glShaderSource(shader, shaderCode)
        GLES20.glCompileShader(shader)

        // Error checking
        val compiled = IntArray(1)
        GLES20.glGetShaderiv(shader, GLES20.GL_COMPILE_STATUS, compiled, 0)
        if (compiled[0] == 0) {
            Log.e("HANS", "Shader Compilation Error: " + GLES20.glGetShaderInfoLog(shader))
            GLES20.glDeleteShader(shader)
            return 0
        }
        return shader
    }
}