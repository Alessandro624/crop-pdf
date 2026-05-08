class PDFViewer {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        if (!this.container) {
            throw new Error(`Container ${containerId} not found`);
        }
        this.initialized = true;
        this.pdfDoc = null;
        this.currentPage = 1;
    }

    async loadPDF(url) {
        if (!window.pdfjsLib) {
            throw new Error('PDF.js library not loaded');
        }
        const loadingTask = pdfjsLib.getDocument(url);
        this.pdfDoc = await loadingTask.promise;
        this.renderPage(1);
    }

    async renderPage(pageNum) {
        if (!this.pdfDoc) return;
        const page = await this.pdfDoc.getPage(pageNum);
        const scale = 1.5;
        const viewport = page.getViewport({ scale });
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        canvas.height = viewport.height;
        canvas.width = viewport.width;
        await page.render({ canvasContext: context, viewport }).promise;
        this.container.innerHTML = '';
        this.container.appendChild(canvas);
    }

    async nextPage() {
        if (this.pdfDoc && this.currentPage < this.pdfDoc.numPages) {
            this.currentPage++;
            await this.renderPage(this.currentPage);
        }
    }

    async prevPage() {
        if (this.pdfDoc && this.currentPage > 1) {
            this.currentPage--;
            await this.renderPage(this.currentPage);
        }
    }
}

window.PDFViewer = PDFViewer;
