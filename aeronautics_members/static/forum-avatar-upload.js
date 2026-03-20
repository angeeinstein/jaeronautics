(function () {
    function clamp(value, minimum, maximum) {
        return Math.min(Math.max(value, minimum), maximum);
    }

    function initAvatarUploader(root) {
        var fileInput = root.querySelector('[data-avatar-file-input]');
        var previewPanel = root.querySelector('[data-avatar-preview-panel]');
        var cropPreview = root.querySelector('[data-avatar-crop-preview]');
        var cropStage = root.querySelector('[data-avatar-stage]');
        var zoomRange = root.querySelector('[data-avatar-zoom-range]');
        var zoomOutButton = root.querySelector('[data-avatar-zoom-out]');
        var zoomInButton = root.querySelector('[data-avatar-zoom-in]');
        var errorBox = root.querySelector('[data-avatar-error]');
        var submitButton = root.querySelector('[data-avatar-submit]');
        var hiddenCropMode = root.querySelector('[data-avatar-crop-mode]');
        var hiddenCropZoom = root.querySelector('[data-avatar-crop-zoom]');
        var hiddenCropCenterX = root.querySelector('[data-avatar-crop-center-x]');
        var hiddenCropCenterY = root.querySelector('[data-avatar-crop-center-y]');
        var maxUploadBytes = parseInt(root.getAttribute('data-max-upload-bytes') || '0', 10);
        var currentImage = null;
        var cropState = {
            zoom: 1,
            centerX: 0.5,
            centerY: 0.5,
            dragging: false,
            pointerId: null,
            lastPointerX: 0,
            lastPointerY: 0
        };

        if (!fileInput || !cropPreview || !cropStage) {
            return;
        }

        function setError(message) {
            if (!errorBox) {
                return;
            }
            if (message) {
                errorBox.textContent = message;
                errorBox.classList.remove('d-none');
                if (submitButton) {
                    submitButton.disabled = true;
                }
            } else {
                errorBox.textContent = '';
                errorBox.classList.add('d-none');
                if (submitButton) {
                    submitButton.disabled = false;
                }
            }
        }

        function resetCropState() {
            cropState.zoom = 1;
            cropState.centerX = 0.5;
            cropState.centerY = 0.5;
            cropState.dragging = false;
            cropState.pointerId = null;
            cropState.lastPointerX = 0;
            cropState.lastPointerY = 0;
            if (zoomRange) {
                zoomRange.value = '1';
            }
            updateHiddenFields();
            cropStage.classList.remove('is-dragging');
        }

        function updateHiddenFields() {
            if (hiddenCropMode) {
                hiddenCropMode.value = 'square';
            }
            if (hiddenCropZoom) {
                hiddenCropZoom.value = String(cropState.zoom);
            }
            if (hiddenCropCenterX) {
                hiddenCropCenterX.value = String(cropState.centerX);
            }
            if (hiddenCropCenterY) {
                hiddenCropCenterY.value = String(cropState.centerY);
            }
        }

        function getPreviewSize() {
            return Math.max(240, Math.round(cropStage.clientWidth || cropPreview.clientWidth || 320));
        }

        function getCropSide() {
            if (!currentImage) {
                return 1;
            }
            var naturalWidth = currentImage.naturalWidth || currentImage.width || 1;
            var naturalHeight = currentImage.naturalHeight || currentImage.height || 1;
            return Math.max(1, Math.min(naturalWidth, naturalHeight) / cropState.zoom);
        }

        function clampCropCenter() {
            if (!currentImage) {
                return;
            }
            var naturalWidth = currentImage.naturalWidth || currentImage.width || 1;
            var naturalHeight = currentImage.naturalHeight || currentImage.height || 1;
            var cropSide = getCropSide();
            var halfWidthRatio = cropSide / (2 * naturalWidth);
            var halfHeightRatio = cropSide / (2 * naturalHeight);
            cropState.centerX = clamp(cropState.centerX, halfWidthRatio, 1 - halfWidthRatio);
            cropState.centerY = clamp(cropState.centerY, halfHeightRatio, 1 - halfHeightRatio);
        }

        function renderPreview() {
            if (!currentImage) {
                return;
            }

            clampCropCenter();
            updateHiddenFields();

            var previewSize = getPreviewSize();
            var naturalWidth = currentImage.naturalWidth || currentImage.width || 1;
            var naturalHeight = currentImage.naturalHeight || currentImage.height || 1;
            var cropSide = getCropSide();
            var centerX = cropState.centerX * naturalWidth;
            var centerY = cropState.centerY * naturalHeight;
            var cropLeft = clamp(Math.round(centerX - cropSide / 2), 0, Math.max(0, naturalWidth - cropSide));
            var cropTop = clamp(Math.round(centerY - cropSide / 2), 0, Math.max(0, naturalHeight - cropSide));

            cropPreview.width = previewSize;
            cropPreview.height = previewSize;
            var context = cropPreview.getContext('2d');
            context.clearRect(0, 0, previewSize, previewSize);
            context.drawImage(currentImage, cropLeft, cropTop, cropSide, cropSide, 0, 0, previewSize, previewSize);
        }

        function applyZoomDelta(delta) {
            var currentZoom = parseFloat((zoomRange && zoomRange.value) || String(cropState.zoom));
            var nextZoom = clamp(currentZoom + delta, 1, 4);
            cropState.zoom = nextZoom;
            if (zoomRange) {
                zoomRange.value = String(nextZoom);
            }
            renderPreview();
        }

        function startDragging(event) {
            if (!currentImage) {
                return;
            }
            cropState.dragging = true;
            cropState.pointerId = event.pointerId;
            cropState.lastPointerX = event.clientX;
            cropState.lastPointerY = event.clientY;
            cropStage.classList.add('is-dragging');
            if (cropStage.setPointerCapture) {
                cropStage.setPointerCapture(event.pointerId);
            }
            event.preventDefault();
        }

        function stopDragging(event) {
            if (!cropState.dragging) {
                return;
            }
            cropState.dragging = false;
            cropStage.classList.remove('is-dragging');
            if (event && cropStage.releasePointerCapture && cropState.pointerId !== null) {
                try {
                    cropStage.releasePointerCapture(cropState.pointerId);
                } catch (_error) {
                    // Ignore pointer release issues.
                }
            }
            cropState.pointerId = null;
        }

        function dragPreview(event) {
            if (!cropState.dragging || !currentImage) {
                return;
            }
            if (cropState.pointerId !== null && event.pointerId !== cropState.pointerId) {
                return;
            }

            var previewSize = getPreviewSize();
            var naturalWidth = currentImage.naturalWidth || currentImage.width || 1;
            var naturalHeight = currentImage.naturalHeight || currentImage.height || 1;
            var cropSide = getCropSide();
            var deltaX = event.clientX - cropState.lastPointerX;
            var deltaY = event.clientY - cropState.lastPointerY;

            cropState.centerX -= (deltaX / previewSize) * (cropSide / naturalWidth);
            cropState.centerY -= (deltaY / previewSize) * (cropSide / naturalHeight);
            cropState.lastPointerX = event.clientX;
            cropState.lastPointerY = event.clientY;
            renderPreview();
            event.preventDefault();
        }

        function handleLoadedImage(imageUrl) {
            var image = new window.Image();
            image.onload = function () {
                currentImage = image;
                resetCropState();
                if (previewPanel) {
                    previewPanel.classList.remove('d-none');
                }
                renderPreview();
            };
            image.onerror = function () {
                currentImage = null;
                if (previewPanel) {
                    previewPanel.classList.add('d-none');
                }
                setError('The selected file could not be previewed. Please choose a valid image.');
            };
            image.src = imageUrl;
        }

        fileInput.addEventListener('change', function (event) {
            setError('');
            resetCropState();
            var file = event.target.files && event.target.files[0];
            if (!file) {
                currentImage = null;
                if (previewPanel) {
                    previewPanel.classList.add('d-none');
                }
                return;
            }

            if (maxUploadBytes && file.size > maxUploadBytes) {
                currentImage = null;
                if (previewPanel) {
                    previewPanel.classList.add('d-none');
                }
                setError('This file is too large to upload. Please choose a smaller image.');
                return;
            }

            var reader = new FileReader();
            reader.onload = function (loadEvent) {
                handleLoadedImage(loadEvent.target.result);
            };
            reader.onerror = function () {
                currentImage = null;
                if (previewPanel) {
                    previewPanel.classList.add('d-none');
                }
                setError('The selected file could not be read. Please try another image.');
            };
            reader.readAsDataURL(file);
        });

        if (zoomRange) {
            zoomRange.addEventListener('input', function () {
                cropState.zoom = clamp(parseFloat(zoomRange.value || '1'), 1, 4);
                renderPreview();
            });
            zoomRange.addEventListener('change', function () {
                cropState.zoom = clamp(parseFloat(zoomRange.value || '1'), 1, 4);
                renderPreview();
            });
        }

        if (zoomOutButton) {
            zoomOutButton.addEventListener('click', function () {
                applyZoomDelta(-0.1);
            });
        }

        if (zoomInButton) {
            zoomInButton.addEventListener('click', function () {
                applyZoomDelta(0.1);
            });
        }

        cropStage.addEventListener('pointerdown', startDragging);
        cropStage.addEventListener('pointermove', dragPreview);
        cropStage.addEventListener('pointerup', stopDragging);
        cropStage.addEventListener('pointercancel', stopDragging);
        cropStage.addEventListener('pointerleave', function (event) {
            if (cropState.dragging) {
                stopDragging(event);
            }
        });

        window.addEventListener('resize', function () {
            if (currentImage) {
                renderPreview();
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('[data-avatar-upload-root]').forEach(initAvatarUploader);
    });
})();
